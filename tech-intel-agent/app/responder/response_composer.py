from app.core.llm_client import call_llm
from app.prompts.responder.response_composer import (
    RESPONSE_COMPOSER_SYSTEM_PROMPT, RESPONSE_COMPOSER_USER_TEMPLATE,
)


async def compose_final_response(draft_answer: str) -> str:
    """
    Final pass after a tool's raw output. Enforces persona
    consistency (no markdown, conversational tone) uniformly across
    whatever tool produced the draft - smalltalk's draft is already
    close to final, while news_db/notification_history/web_search
    drafts may carry slightly more formal source-citation phrasing
    that benefits from this pass smoothing it into one consistent
    voice before it reaches the user.
    """
    result = await call_llm(
        system_prompt=RESPONSE_COMPOSER_SYSTEM_PROMPT,
        user_message=RESPONSE_COMPOSER_USER_TEMPLATE.format(draft_answer=draft_answer),
        trace_name="response-composer",
        temperature=0.5,
        max_tokens=300,
    )
    return result.content
