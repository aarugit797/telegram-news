from app.core.llm_client import call_llm
from app.db.repository_conversation import get_recent_messages, get_latest_summary, save_summary
from app.db.session_conversation import get_conversation_session

RAW_MESSAGES_TO_KEEP = 5
MESSAGES_BEFORE_SUMMARIZING = 10

SUMMARIZE_SYSTEM_PROMPT = (
    "Summarize this conversation history into one short paragraph, "
    "preserving any facts or context that later messages might reference."
)


async def get_recent_turns(user_id, turns: int = 3) -> str:
    """
    The last few raw messages, for the intent classifier.

    Deliberately NOT get_conversation_context: that one may fire the
    history summarizer, and the classifier does not need a summary of
    last week to tell whether "is it useful for me?" contains a pronoun.
    It needs the turn immediately before. This is one indexed read and no
    LLM call.

    SKIPS THE NEWEST ROW. process_message saves the inbound message
    before classifying, so the most recent row IS the message being
    classified - including it would just show the model its own input
    twice and push the actual referent out of the window.
    """
    async with get_conversation_session() as session:
        recent = await get_recent_messages(session, user_id, limit=turns + 1)

    previous = recent[1:] if recent else []
    return "\n".join(f"[{m.direction}] {m.message_text}" for m in reversed(previous))


async def get_conversation_context(user_id) -> str:
    """
    Called before the conversational agent runs. Returns one string
    ready to inject into that turn's prompt.

    If history is still short, returns the raw recent messages
    directly. Once history grows past MESSAGES_BEFORE_SUMMARIZING,
    reuses an existing summary if one already covers the older
    messages, or generates a fresh one - then returns that summary
    plus only the most recent RAW_MESSAGES_TO_KEEP raw messages. This
    keeps the context sent to the LLM bounded regardless of how long
    a user's conversation history grows, rather than blindly sending
    more and more raw messages over time.
    """
    async with get_conversation_session() as session:
        recent = await get_recent_messages(session, user_id, limit=MESSAGES_BEFORE_SUMMARIZING)

        if len(recent) < MESSAGES_BEFORE_SUMMARIZING:
            return "\n".join(f"[{m.direction}] {m.message_text}" for m in reversed(recent))

        existing_summary = await get_latest_summary(session, user_id)
        recent_raw = recent[:RAW_MESSAGES_TO_KEEP]
        raw_text = "\n".join(f"[{m.direction}] {m.message_text}" for m in reversed(recent_raw))
        boundary_message = recent[RAW_MESSAGES_TO_KEEP]

        if existing_summary and existing_summary.covers_to >= boundary_message.timestamp:
            return f"Earlier context: {existing_summary.summary_text}\n\nRecent messages:\n{raw_text}"

        older_messages = recent[RAW_MESSAGES_TO_KEEP:]
        older_text = "\n".join(f"[{m.direction}] {m.message_text}" for m in reversed(older_messages))

        result = await call_llm(
            system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            user_message=older_text,
            trace_name="history-summarizer",
            temperature=0.0,
            # Reserved lane - a user is waiting on this reply.
            lane="responder",
        )

        await save_summary(session, {
            "user_id": user_id,
            "summary_text": result.content,
            "covers_from": older_messages[-1].timestamp,
            "covers_to": older_messages[0].timestamp,
        })

        return f"Earlier context: {result.content}\n\nRecent messages:\n{raw_text}"
