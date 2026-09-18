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
        # 1024, not a tight cap. max_tokens looks like a brevity lever but
        # is an unreliable one here: reasoning tokens are billed against
        # it and never returned, so a tight ceiling does not shorten the
        # answer - it truncates or empties it. This exact call at 300
        # failed with "empty response from Groq" on a live user message.
        #
        # Length is the PROMPT's job (it already asks for 1-3 sentences),
        # and this is a safety ceiling rather than a style control.
        max_tokens=4096,
        # Reserved lane - a user is waiting on this reply.
        lane="responder",
    )
    return result.content
