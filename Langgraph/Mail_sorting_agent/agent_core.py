"""Core logic for the email agent: Gmail access, LLM, classification, and the
LangGraph email workflow. Imported by tools.py and app.py (and reusable anywhere).

This is the same logic proven in workflow.ipynb, lifted into one importable module.
Gmail auth reuses credentials.json / token.json (scopes: gmail.readonly + gmail.compose),
so nothing here sends mail or mutates the mailbox — replies are created as *drafts* only.
"""

import os
import re
import base64
from operator import add
from typing import TypedDict, List, Dict, Any, Optional, Literal, Annotated
from email.mime.text import MIMEText

from dotenv import load_dotenv
from pydantic import BaseModel, Field
from langchain_groq import ChatGroq
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
import httplib2
from google_auth_oauthlib.flow import InstalledAppFlow
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from google_auth_httplib2 import AuthorizedHttp
from googleapiclient.discovery import build

# Run relative to this file so credentials.json / token.json resolve no matter the CWD.
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv()  # GROQ_API_KEY, LANGFUSE_* from the repo-root .env

GMAIL_SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",   # read & list messages
    "https://www.googleapis.com/auth/gmail.compose",    # create draft replies
]


# --------------------------------------------------------------------------- #
# Gmail auth + service
# --------------------------------------------------------------------------- #
def get_credentials(client_secrets="credentials.json", token_cache="token.json"):
    client_secrets = os.path.join(_HERE, client_secrets)
    token_cache = os.path.join(_HERE, token_cache)
    creds = None
    if os.path.exists(token_cache):
        creds = Credentials.from_authorized_user_file(token_cache, GMAIL_SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(client_secrets, GMAIL_SCOPES)
            creds = flow.run_local_server(port=0)
        with open(token_cache, "w") as f:
            f.write(creds.to_json())
    return creds


_creds = get_credentials()
gmail = build("gmail", "v1", credentials=_creds, cache_discovery=False)


def _http():
    """A fresh authorized http per request.

    httplib2 (googleapiclient's transport) is NOT thread-safe, and LangGraph runs tool
    nodes in worker threads — a shared connection corrupts (SSL WRONG_VERSION_NUMBER).
    Passing a brand-new AuthorizedHttp to each .execute() keeps every call isolated.
    """
    return AuthorizedHttp(_creds, http=httplib2.Http())


# --------------------------------------------------------------------------- #
# Gmail helpers
# --------------------------------------------------------------------------- #
def list_unread(max_results=5):
    """Return [{id, threadId}, ...] for recent unread inbox mail."""
    res = gmail.users().messages().list(
        userId="me", q="is:unread -in:chats", maxResults=max_results
    ).execute(http=_http())
    return res.get("messages", [])


def search_messages(query="is:unread", max_results=5):
    """Return [{id, threadId}, ...] for any Gmail search query."""
    res = gmail.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute(http=_http())
    return res.get("messages", [])


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data.encode()).decode("utf-8", errors="replace")


def _extract_body(payload: dict) -> str:
    """Depth-first search for the first text/plain body in a message payload."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return _decode(payload["body"]["data"])
    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text
    return ""


def get_email(msg_id: str) -> dict:
    """Fetch one message and flatten it to {id, thread_id, sender, subject, body}."""
    m = gmail.users().messages().get(userId="me", id=msg_id, format="full").execute(http=_http())
    headers = {h["name"].lower(): h["value"] for h in m["payload"].get("headers", [])}
    return {
        "id": m["id"],
        "thread_id": m["threadId"],
        "sender": headers.get("from", ""),
        "subject": headers.get("subject", "(no subject)"),
        "body": _extract_body(m["payload"]) or m.get("snippet", ""),
    }


def _plain_email(addr: Optional[str]) -> str:
    """Extract a bare address from a 'Name <email>' header value."""
    if not addr:
        return ""
    m = re.search(r"[\w.+-]+@[\w.-]+\.[\w.-]+", addr)
    return m.group(0) if m else addr


def create_draft_reply(to: str, subject: str, body: str, thread_id: str) -> dict:
    """Create a threaded draft reply in Gmail. Returns the created draft resource."""
    msg = MIMEText(body)
    msg["to"] = _plain_email(to)
    msg["subject"] = subject
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
    return gmail.users().drafts().create(
        userId="me", body={"message": {"raw": raw, "threadId": thread_id}}
    ).execute(http=_http())


# --------------------------------------------------------------------------- #
# LLM + classification
# --------------------------------------------------------------------------- #
llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.2,
    max_tokens=1000,
    api_key=os.environ.get("GROQ_API_KEY"),
)


class EmailClassification(BaseModel):
    # Groq validates tool-call args strictly and llama emits strings, so a raw `bool`
    # fails ("expected boolean, but got string"). Use a yes/no enum and convert in code.
    spam: Literal["yes", "no"] = Field(
        description="'yes' if the email is spam / junk / phishing / mass promotion, else 'no'."
    )
    category: Literal["inquiry", "personal", "notification", "promotion", "spam", "other"] = Field(
        description="Best-fit category for the email."
    )
    reason: str = Field(description="Short justification for the classification.")


def _email_text(email: Dict[str, Any]) -> str:
    return (
        f"From: {email.get('sender', 'unknown')}\n"
        f"Subject: {email.get('subject', '(no subject)')}\n\n"
        f"{email.get('body', '')}"
    )


def classify(email: Dict[str, Any]) -> EmailClassification:
    """LLM structured classification of one email dict."""
    classifier = llm.with_structured_output(EmailClassification)
    return classifier.invoke([
        HumanMessage(content=(
            "Classify the following email. Decide whether it is spam and pick its category.\n\n"
            + _email_text(email)
        ))
    ])


def write_reply(email: Dict[str, Any]) -> str:
    """LLM draft body for a legitimate email."""
    resp = llm.invoke([
        HumanMessage(content=(
            "You are an assistant drafting a concise, professional reply to the email below. "
            "Return only the reply body — no subject line, no preamble.\n\n"
            + _email_text(email)
        ))
    ])
    return resp.content


# --------------------------------------------------------------------------- #
# LangGraph email workflow
# --------------------------------------------------------------------------- #
class EmailState(TypedDict):
    email: Dict[str, Any]
    email_category: Optional[str]
    spam_reason: Optional[str]
    is_spam: Optional[bool]
    email_draft: Optional[str]
    draft_id: Optional[str]
    messages: Annotated[List[Dict[str, Any]], add]


def _read_email(state: EmailState) -> EmailState:
    email = state["email"]
    return {"messages": [{"role": "user", "content": _email_text(email)}]}


def _categorize_email(state: EmailState) -> EmailState:
    result = classify(state["email"])
    is_spam = result.spam == "yes"
    return {
        "is_spam": is_spam,
        "email_category": "spam" if is_spam else result.category,
        "spam_reason": result.reason if is_spam else None,
    }


def _report_spam_reason(state: EmailState) -> EmailState:
    reason = state.get("spam_reason") or "Detected as spam."
    return {"messages": [{"role": "system", "content": f"spam: {reason}"}]}


def _draft_email_response(state: EmailState) -> EmailState:
    draft = write_reply(state["email"])
    return {"email_draft": draft, "messages": [{"role": "assistant", "content": draft}]}


def _send_email_response(state: EmailState) -> EmailState:
    email = state["email"]
    result = create_draft_reply(
        to=email.get("sender", ""),
        subject="Re: " + (email.get("subject") or ""),
        body=state.get("email_draft", ""),
        thread_id=email.get("thread_id"),
    )
    return {"draft_id": result.get("id"),
            "messages": [{"role": "system", "content": f"draft_id: {result.get('id')}"}]}


def _route_email(state: EmailState) -> str:
    return "report_spam_reason" if state.get("is_spam") else "draft_email_response"


def _build_email_graph():
    g = StateGraph(EmailState)
    g.add_node("read_email", _read_email)
    g.add_node("categorize_email", _categorize_email)
    g.add_node("report_spam_reason", _report_spam_reason)
    g.add_node("draft_email_response", _draft_email_response)
    g.add_node("send_email_response", _send_email_response)
    g.add_edge(START, "read_email")
    g.add_edge("read_email", "categorize_email")
    g.add_conditional_edges("categorize_email", _route_email, {
        "report_spam_reason": "report_spam_reason",
        "draft_email_response": "draft_email_response",
    })
    g.add_edge("report_spam_reason", END)
    g.add_edge("draft_email_response", "send_email_response")
    g.add_edge("send_email_response", END)
    return g.compile()


email_graph = _build_email_graph()


def run_full_workflow(max_results: int = 5) -> List[Dict[str, Any]]:
    """Run the full LangGraph pipeline over recent unread mail.

    Returns a summary list: [{sender, subject, category, is_spam, draft_id}, ...].
    """
    summaries = []
    for ref in list_unread(max_results=max_results):
        email = get_email(ref["id"])
        final = email_graph.invoke({
            "email": email,
            "email_category": None, "spam_reason": None, "is_spam": None,
            "email_draft": None, "draft_id": None, "messages": [],
        })
        summaries.append({
            "sender": email["sender"],
            "subject": email["subject"],
            "category": final.get("email_category"),
            "is_spam": final.get("is_spam"),
            "draft_id": final.get("draft_id"),
        })
    return summaries