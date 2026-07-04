"""Chainlit chat UI for the email agent.

Run:  chainlit run app.py -w      → opens http://localhost:8000

A LangGraph ReAct agent chats with you and calls the email tools (search_inbox, read_email,
classify_email, draft_reply, run_email_workflow) when your message asks for them. You can
invoke any tool independently ("classify email <id>") or run the whole pipeline
("run the workflow on 5 emails"). Every tool call is shown as a nested step.
"""

import chainlit as cl
from langchain.agents import create_agent
from langgraph.checkpoint.memory import MemorySaver

from agent_core import llm
from tools import TOOLS

SYSTEM_PROMPT = (
    "You are an email assistant for the user's Gmail inbox. You have tools to search the "
    "inbox, read an email, classify it (spam / category), draft a reply, and run the full "
    "email workflow over unread mail.\n"
    "- When the user asks about their mail, call the appropriate tool rather than guessing.\n"
    "- Tools that reference a specific email need its message id; if you don't have one, use "
    "search_inbox first to find it.\n"
    "- ONLY take actions the user explicitly asked for. Do NOT create drafts or run the "
    "workflow unless clearly requested. For 'list'/'read'/'what's in my inbox' questions, just "
    "search/read and summarize — never draft replies unprompted.\n"
    "- Safety: you can only create Gmail DRAFTS — you never send email or change labels. Tell "
    "the user drafts are saved for them to review and send.\n"
    "- Keep replies concise and report tool results plainly."
)

WELCOME = (
    "👋 **Email agent ready.** Ask me things like:\n"
    "- *What's in my unread inbox?*\n"
    "- *Classify email &lt;id&gt;* or *read email &lt;id&gt;*\n"
    "- *Draft a reply to the Groq email*\n"
    "- *Run the full workflow on my 5 newest unread emails*\n\n"
    "_I only create drafts — nothing is ever sent automatically._"
)


@cl.on_chat_start
async def on_chat_start():
    agent = create_agent(
        llm,
        TOOLS,
        system_prompt=SYSTEM_PROMPT,
        checkpointer=MemorySaver(),
    )
    cl.user_session.set("agent", agent)
    await cl.Message(content=WELCOME).send()


@cl.on_message
async def on_message(message: cl.Message):
    agent = cl.user_session.get("agent")
    config = {
        "configurable": {"thread_id": cl.context.session.id},   # per-session memory
        "callbacks": [cl.LangchainCallbackHandler()],           # renders tool steps
    }
    result = await agent.ainvoke(
        {"messages": [{"role": "user", "content": message.content}]},
        config=config,
    )
    await cl.Message(content=result["messages"][-1].content).send()