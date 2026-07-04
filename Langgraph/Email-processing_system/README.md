# 📧 Email Processing Agent (LangGraph + Gmail)

A small **LangGraph** agent that reads unread Gmail, uses a **Groq LLM** to classify each
email (spam vs. category), and **drafts a reply** for legitimate mail — leaving the draft in
Gmail for you to review and send.

```
          ┌─────────────┐     ┌──────────────────┐
 START ─▶ │ read_email  │ ──▶ │ categorize_email │ ──┐  (LLM: spam? category?)
          └─────────────┘     └──────────────────┘   │
                                                      ▼
                                              route_email()
                                is_spam? ──yes──▶ report_spam_reason ─▶ END
                                    │
                                    └──no──▶ draft_email_response ─▶ send_email_response ─▶ END
                                              (LLM writes reply)      (creates Gmail draft)
```

---

## The Gmail integration story (MCP → direct API)

This project was **meant** to talk to Gmail through the hosted **Google Gmail MCP server**
(`https://gmailmcp.googleapis.com/mcp/v1`) via `langchain-mcp-adapters`. That path is
implemented and correct, but it is currently **blocked by a preview gate**, so the notebook
now uses the **direct Gmail REST API** instead. Here is the full journey, so nothing is lost.

### Attempt 1 — Hosted Gmail MCP server (blocked)

- **What it is:** Google's remote MCP server that exposes Gmail as MCP tools
  (`search_threads`, `get_thread`, `create_draft`, `apply_sensitive_message_label`, …).
- **What we set up correctly:**
  - Enabled **both** `gmail.googleapis.com` and `gmailmcp.googleapis.com` in the Cloud project.
  - OAuth consent screen with scopes `gmail.readonly` + `gmail.compose`, self as a test user.
  - A valid OAuth access token (auto-refreshed) sent as `Authorization: Bearer <token>`.
- **The wall:** every tool call returned **`"The caller does not have permission"`**.
  We proved this was **not** a code/token/scope problem — the *same token* calls the plain
  Gmail REST API successfully. The blocker is that the hosted Gmail MCP server is a
  **Google Workspace Developer Preview** feature and requires enrollment in the
  [Developer Preview Program](https://developers.google.com/workspace/preview).
- **Catch:** the preview program generally needs a **Google Workspace** account; a personal
  `@gmail.com` typically cannot enroll. So this path is on hold.

**Real Gmail MCP tool schema** (kept here for when the preview is available). It is
thread-based and uses camelCase args; results come back as MCP content blocks
(unwrap the text → `json.loads`):

| Tool | Key args | Returns |
|------|----------|---------|
| `search_threads` | `query` (Gmail syntax), `pageSize` | `{ threads: [{ id, messages:[…] }] }` |
| `get_thread` | `threadId`, `messageFormat="FULL_CONTENT"` | messages with `sender, subject, plaintextBody, snippet, id` |
| `create_draft` | `to` (**array** of bare emails), `subject`, `body`, `replyToMessageId` | created draft (⚠️ **no send tool — draft only**) |
| `apply_sensitive_message_label` | `messageId`, `labelOption="SPAM"\|"TRASH"` | — |

### Attempt 2 — Direct Gmail REST API (current, working) ✅

Same OAuth credentials, no preview needed. The notebook builds a Gmail service with
`googleapiclient.discovery.build("gmail", "v1", credentials=...)` and uses three tiny helpers:

| Helper | Gmail API call |
|--------|----------------|
| `list_unread(max_results)` | `users().messages().list(q="is:unread -in:chats")` |
| `get_email(msg_id)` | `users().messages().get(format="full")` → flatten to `{sender, subject, body, …}` |
| `create_draft_reply(to, subject, body, thread_id)` | `users().drafts().create(...)` (threaded, base64 raw MIME) |

The LangGraph workflow (state, nodes, routing, LLM classification) is **identical** across
both approaches — only the ~3 Gmail calls differ.

---

## Setup

### 1. Google Cloud (one time)
1. **Enable the Gmail API** — APIs & Services → Library → *Gmail API* → **Enable**.
2. **OAuth consent screen** — add scopes
   `https://www.googleapis.com/auth/gmail.readonly` and
   `https://www.googleapis.com/auth/gmail.compose`; add yourself as a **test user**.
3. **Credentials** → Create OAuth client ID → **Desktop app** → download JSON,
   rename to **`credentials.json`**, place it in this folder (next to `workflow.ipynb`).

### 2. Environment
Copy `.env.example` → repo-root `.env` and set:
- `GROQ_API_KEY` — required (LLM).
- `LANGFUSE_*` — optional (tracing).

`.env`, `credentials.json`, and `token.json` are all **gitignored** — never commit them.

### 3. Run
Open `workflow.ipynb` and run top-to-bottom. On first run the auth cell opens a browser
once for consent and caches **`token.json`** (auto-refreshed afterwards). The auth cell
prints the account it connected to.

> **Note (Windows / Python 3.14):** the notebook kernel is Python 3.14. After the first
> `pip install`, **restart the kernel** so freshly installed packages load.

---

## Behavior & safety
- **Draft-only:** replies are created as Gmail **drafts** — nothing is sent automatically.
- **Spam is non-destructive:** the spam path only logs the reason; it does **not** trash or
  relabel your mail (keeps scopes to `readonly` + `compose`).

## Files
| File | Purpose |
|------|---------|
| `workflow.ipynb` | The agent (auth → helpers → LangGraph workflow → run). |
| `.env.example` | Template for the repo-root `.env`. |
| `credentials.json` | Your OAuth client (git-ignored, you add it). |
| `token.json` | Cached OAuth token (git-ignored, auto-created). |

## Stack
LangGraph · LangChain · Groq (`llama-3.3-70b-versatile`) · google-api-python-client ·
Langfuse (optional) · Python 3.14
