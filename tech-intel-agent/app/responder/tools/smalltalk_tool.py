from app.core.llm_client import call_llm
from app.prompts.responder.smalltalk import SMALLTALK_SYSTEM_PROMPT, SMALLTALK_USER_TEMPLATE


async def run_smalltalk_tool(message: str, context: str = "") -> str:
    """
    No database call at all - direct LLM response with the persona
    prompt. Highest temperature-appropriate of the classification
    calls, but still uses call_llm's plain-text path (no json_schema)
    since this wants natural conversational text, not structured data.
    """
    result = await call_llm(
        system_prompt=SMALLTALK_SYSTEM_PROMPT,
        user_message=SMALLTALK_USER_TEMPLATE.format(
            context=context or "(no earlier messages)", message=message
        ),
        trace_name="smalltalk-tool",
        temperature=0.7,
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
