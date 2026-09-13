from app.core.llm_client import call_llm
from app.db.repository_conversation import get_recent_messages
from app.db.session_conversation import get_conversation_session
from app.prompts.responder.guardrail import (
    GUARDRAIL_SYSTEM_PROMPT, GUARDRAIL_USER_TEMPLATE, GUARDRAIL_JSON_SCHEMA,
)

HISTORY_MESSAGES_FOR_GUARDRAIL = 5


async def check_guardrail(user_id, current_message: str) -> str:
    """
    Runs before Intent Classifier - the current message is checked
    ALONGSIDE the last 5 messages (not in isolation), since a
    prompt-injection attempt can be built up gradually across turns.
    Returns "PASS" | "INJECTION_DETECTED" | "OFF_TOPIC".
    """
    async with get_conversation_session() as session:
        recent = await get_recent_messages(session, user_id, limit=HISTORY_MESSAGES_FOR_GUARDRAIL)

    history_text = "\n".join(f"[{m.direction}] {m.message_text}" for m in reversed(recent)) or "(no prior history)"

    result = await call_llm(
        system_prompt=GUARDRAIL_SYSTEM_PROMPT,
        user_message=GUARDRAIL_USER_TEMPLATE.format(history=history_text, message=current_message),
        trace_name="guardrail-check",
        json_schema=GUARDRAIL_JSON_SCHEMA,
        temperature=0.0,
        # Reserved lane - a user is waiting on this reply.
        lane="responder",
    )
    return result.content["classification"]
