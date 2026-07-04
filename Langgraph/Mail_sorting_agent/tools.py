"""LangChain tools exposing the email agent's capabilities.

Each tool is independently callable (e.g. `search_inbox.invoke({...})`) and is also given
to the chat agent, which decides which to call from the user's message. Tools return plain
strings so they render cleanly in the chat UI. Draft-only: nothing here sends mail.
"""

from langchain_core.tools import tool

import agent_core as core


@tool
def search_inbox(query: str = "is:unread", max_results: int = 5) -> str:
    """Search Gmail and list matching messages. `query` uses Gmail search syntax
    (e.g. "is:unread", "from:groq.com", "subject:invoice newer_than:7d").
    Returns each match as: <message_id> · <from> · <subject>."""
    refs = core.search_messages(query=query, max_results=max_results)
    if not refs:
        return f"No messages found for query: {query!r}"
    lines = []
    for ref in refs:
        e = core.get_email(ref["id"])
        lines.append(f"{e['id']} · {e['sender']} · {e['subject']}")
    return f"Found {len(lines)} message(s):\n" + "\n".join(lines)


@tool
def read_email(message_id: str) -> str:
    """Read one email in full by its message id. Returns sender, subject, and body text."""
    e = core.get_email(message_id)
    return (f"From: {e['sender']}\nSubject: {e['subject']}\n\n{e['body']}").strip()


@tool
def classify_email(message_id: str) -> str:
    """Classify one email by id: whether it is spam and its best-fit category, with a reason."""
    e = core.get_email(message_id)
    r = core.classify(e)
    is_spam = r.spam == "yes"
    return (f"Subject: {e['subject']}\n"
            f"Spam: {is_spam}\nCategory: {'spam' if is_spam else r.category}\nReason: {r.reason}")


@tool
def draft_reply(message_id: str) -> str:
    """Write a professional reply to one email (by id) and save it as a Gmail *draft*
    (threaded to the original). Returns the draft id and a preview. Does not send."""
    e = core.get_email(message_id)
    body = core.write_reply(e)
    result = core.create_draft_reply(
        to=e["sender"],
        subject="Re: " + (e["subject"] or ""),
        body=body,
        thread_id=e["thread_id"],
    )
    return (f"✅ Draft saved (id: {result.get('id')}) replying to {e['sender']}.\n\n"
            f"--- draft preview ---\n{body}")


@tool
def run_email_workflow(max_results: int = 5) -> str:
    """Run the full email workflow over recent unread mail: read → classify → for spam,
    report the reason; for legitimate mail, draft a reply saved to Gmail. Returns a summary."""
    summaries = core.run_full_workflow(max_results=max_results)
    if not summaries:
        return "No unread emails to process."
    lines = []
    for s in summaries:
        if s["is_spam"]:
            lines.append(f"🚫 {s['subject']} — spam ({s['category']})")
        else:
            lines.append(f"✍️  {s['subject']} — {s['category']}, draft saved (id: {s['draft_id']})")
    return f"Processed {len(summaries)} email(s):\n" + "\n".join(lines)


TOOLS = [search_inbox, read_email, classify_email, draft_reply, run_email_workflow]