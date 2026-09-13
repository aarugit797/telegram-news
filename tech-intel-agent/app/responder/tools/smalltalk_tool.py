from app.core.llm_client import call_llm
from app.prompts.responder.smalltalk import SMALLTALK_SYSTEM_PROMPT, SMALLTALK_USER_TEMPLATE


async def run_smalltalk_tool(message: str) -> str:
    """
    No database call at all - direct LLM response with the persona
    prompt. Highest temperature-appropriate of the classification
    calls, but still uses call_llm's plain-text path (no json_schema)
    since this wants natural conversational text, not structured data.
    """
    result = await call_llm(
        system_prompt=SMALLTALK_SYSTEM_PROMPT,
        user_message=SMALLTALK_USER_TEMPLATE.format(message=message),
        trace_name="smalltalk-tool",
        temperature=0.7,
        max_tokens=150,
        # Reserved lane - a user is waiting on this reply.
        lane="responder",
    )
    return result.content
