# 💬 Chat UI + Tool-Calling — Design & Usage

A **Chainlit** chat interface over the email agent. You chat in natural language; a LangGraph
**ReAct agent** decides which tool to call. You can invoke any tool on its own
("classify email &lt;id&gt;") or run the whole pipeline ("run the workflow on 5 emails").
Every tool call renders as a nested step in the chat.

## Architecture

```
  Browser (Chainlit chat)
        │  your message
        ▼
  app.py  ──►  create_agent(llm, TOOLS, checkpointer=MemorySaver)   ← LangGraph ReAct agent
        │            │ decides which tool(s) to call from your message
        │            ▼
        │        tools.py   (5 LangChain @tool wrappers)
        │            ▼
        └──────  agent_core.py   (Gmail service · helpers · LLM · email_graph)
                     ▼
             Gmail REST API  +  Groq LLM
```

| File | Role |
|------|------|
| `agent_core.py` | Gmail auth/service, helpers, Groq LLM, classification, the LangGraph `email_graph`, and `run_full_workflow()`. Single source of truth. |
| `tools.py` | 5 `@tool`s exposing those capabilities. `TOOLS` list. |
| `app.py` | Chainlit UI: builds the agent, streams tool steps, per-session memory. |
| `workflow.ipynb` | The original notebook (kept as the step-by-step teaching version). |

## The tools

| Tool | What it does | Example trigger |
|------|--------------|-----------------|
| `search_inbox(query, max_results)` | List messages matching a Gmail query | "what's in my unread inbox?" |
| `read_email(message_id)` | Full sender/subject/body | "read email &lt;id&gt;" |
| `classify_email(message_id)` | Spam? + category + reason | "is email &lt;id&gt; spam?" |
| `draft_reply(message_id)` | Write a reply → save Gmail **draft** | "draft a reply to the Groq email" |
| `run_email_workflow(max_results)` | Full pipeline over unread mail | "run the workflow on my 5 newest unread" |

The agent chains tools automatically (e.g. `search_inbox` → `draft_reply`) and only **acts**
(drafts / runs the workflow) when you explicitly ask — plain "list/read" requests never create
drafts.

## Run it

```bash
pip install chainlit          # one time (langchain / langgraph / groq already installed)
chainlit run app.py -w        # -w = auto-reload on edits
# opens http://localhost:8000
```

Requirements (same as the notebook): `credentials.json` + `token.json` in this folder, and
`GROQ_API_KEY` in the repo-root `.env`. First launch opens a browser once for Google consent.

## Example prompts
- *What's in my unread inbox?*  → `search_inbox`
- *Read email &lt;id&gt;*  /  *Classify email &lt;id&gt;*  → `read_email` / `classify_email`
- *Draft a reply to the Groq email*  → `search_inbox` then `draft_reply`
- *Run the full workflow on my 5 newest unread emails*  → `run_email_workflow`
- Follow-ups work too — memory persists per chat session (*"now draft a reply to that one"*).

## Safety
- **Draft-only.** Tools never send mail or change labels (scopes stay `readonly` + `compose`).
- The agent is instructed to act only on explicit requests, so listing/reading won't silently
  create drafts.

## Implementation notes
- **Thread safety:** `googleapiclient`'s httplib2 transport isn't thread-safe, and LangGraph
  runs tool nodes in worker threads. Each Gmail call uses a fresh `AuthorizedHttp` (see
  `agent_core._http()`) to avoid `SSL: WRONG_VERSION_NUMBER` corruption.
- **Structured output:** classification uses a `Literal["yes","no"]` spam field (not `bool`) —
  Groq's strict tool-arg validation rejects string-typed booleans from llama models.
- **Model:** Groq `llama-3.3-70b-versatile` (free, reliable tool-calling).