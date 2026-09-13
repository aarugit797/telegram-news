from app.core.llm_client import call_llm
from app.db.repository_news import get_most_recent_batch, get_signals_by_ids
from app.db.session_news import get_news_session
from app.prompts.responder.notification_followup import (
    NOTIFICATION_FOLLOWUP_SYSTEM_PROMPT, NOTIFICATION_FOLLOWUP_USER_TEMPLATE,
)


async def run_notification_history_tool(question: str, user_id: str) -> tuple[str, list]:
    """
    Answers follow-up questions about what was already sent. Since
    v1 sends an identical batch to every active user (no
    personalization), this just retrieves the most recent DELIVERED
    batch overall - user_id is accepted for interface consistency
    with the other tools and for future personalization, but isn't
    used to look up a per-user batch link, since none exists yet.
    """
    async with get_news_session() as session:
        batch = await get_most_recent_batch(session)
        if not batch:
            return "I haven't sent anything yet, so there's nothing to follow up on.", []

        signals = await get_signals_by_ids(session, batch.signal_ids)

    if not signals:
        return "I couldn't find the details from that notification.", []

    signals_text = "\n\n".join(
        f"Title: {s.title}\nSummary: {s.summary}\nDetails: {s.full_content[:800]}"
        for s in signals
    )

    result = await call_llm(
        system_prompt=NOTIFICATION_FOLLOWUP_SYSTEM_PROMPT,
        user_message=NOTIFICATION_FOLLOWUP_USER_TEMPLATE.format(signals=signals_text, question=question),
        trace_name="notification-history-tool",
        temperature=0.3,
        # Reserved lane - a user is waiting on this reply.
        lane="responder",
    )

    return result.content, signals
