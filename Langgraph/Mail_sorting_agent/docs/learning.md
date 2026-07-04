# 🧠 Build Journey & Learning Notes

The story of how this agent came together — the Gmail integration path, the dead-ends, and the
decisions. Kept as a learning record. For the project overview see the [README](../README.md);
for the chat UI see [UI_PLAN.md](UI_PLAN.md).

## The workflow

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
implemented and correct, but it is currently **blocked by a preview gate**, so it now uses the
**direct Gmail REST API** instead. Here is the full journey, so nothing is lost.

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

Same OAuth credentials, no preview needed. A Gmail service is built with
`googleapiclient.discovery.build("gmail", "v1", credentials=...)` and three tiny helpers:

| Helper | Gmail API call |
|--------|----------------|
| `list_unread(max_results)` | `users().messages().list(q="is:unread -in:chats")` |
| `get_email(msg_id)` | `users().messages().get(format="full")` → flatten to `{sender, subject, body, …}` |
| `create_draft_reply(to, subject, body, thread_id)` | `users().drafts().create(...)` (threaded, base64 raw MIME) |

The LangGraph workflow (state, nodes, routing, LLM classification) is **identical** across
both approaches — only the ~3 Gmail calls differ.

---

## Gotchas worth remembering

- **Groq structured output:** Groq strictly validates tool-call args and llama models emit
  strings, so a Pydantic `bool` field fails with *"expected boolean, but got string."* Use a
  `Literal["yes","no"]` enum and convert to bool in code.
- **httplib2 thread-safety:** `googleapiclient`'s transport isn't thread-safe, and LangGraph
  runs tool nodes in worker threads → `SSL: WRONG_VERSION_NUMBER`. Pass a fresh
  `AuthorizedHttp` to every `.execute(http=...)` (see `agent_core._http()`).
- **ReAct over-action:** the chat agent will auto-draft replies on a plain "list my inbox"
  request unless the system prompt says to act only when explicitly asked.
- **Windows / Python 3.14:** after a fresh `pip install` of a package with compiled/DLL parts
  (e.g. `pywin32`), **restart the Jupyter kernel** so it loads.

## Google Cloud setup (one time)
1. **Enable the Gmail API** — APIs & Services → Library → *Gmail API* → **Enable**.
2. **OAuth consent screen** — add scopes `gmail.readonly` + `gmail.compose`; add yourself as a
   **test user**.
3. **Credentials** → Create OAuth client ID → **Desktop app** → download JSON, rename to
   **`credentials.json`**, place it in the project folder. First run opens a browser once and
   caches `token.json` (auto-refreshed after).