import json
from dataclasses import dataclass

from anthropic import AsyncAnthropic
from langsmith import trace
from langsmith.wrappers import wrap_anthropic

from app.core.config import settings

_raw_client = AsyncAnthropic(
    api_key=settings.anthropic_api_key,
    timeout=settings.llm_timeout_seconds,
)
client = wrap_anthropic(_raw_client)

DEFAULT_MODEL = settings.default_llm_model
_TOOL_NAME = "return_structured_output"


@dataclass
class LLMResult:
    """
    Single source of truth for what any caller (like the future
    cost_tracker.py) needs after an LLM call.

    Now captures all FOUR usage fields Anthropic returns, not just
    input/output. cache_creation_input_tokens and
    cache_read_input_tokens exist specifically because of prompt
    caching (see _cached_system_block below) - without capturing
    these two, we'd have no way to actually verify caching is doing
    anything, or to account for its (cheaper) cost correctly later
    in cost_tracker.py.
    """
    content: dict | str
    input_tokens: int
    output_tokens: int
    cache_creation_input_tokens: int
    cache_read_input_tokens: int


def _build_forced_json_tool(json_schema: dict) -> dict:
    return {
        "name": _TOOL_NAME,
        "description": "Return the result in this exact structured format.",
        "input_schema": json_schema,
    }


def _cached_system_block(system_prompt: str) -> list[dict]:
    """
    PROMPT CACHING.

    Every filter/classifier in our system sends the SAME system
    prompt on every call - e.g. GITHUB_FILTER_SYSTEM_PROMPT never
    changes, only the short user_message (this specific repo's
    details) changes each time. That system prompt gets sent,
    unchanged, potentially hundreds of times a day across our 5
    agents plus guardrail plus intent classifier.

    Marking it with cache_control tells Anthropic to cache it
    server-side after the first call. Any later call within the
    cache's short lifetime that reuses this EXACT text pays roughly
    90% less for those tokens (a "cache read") instead of full
    input price (a "cache write" the first time, or a plain
    uncached read if caching isn't used at all).

    system must be passed as a list of content blocks - not a plain
    string - for cache_control to attach to it at all. This is why
    call_llm below builds this block instead of passing
    system=system_prompt directly like our first version did.
    """
    return [
        {
            "type": "text",
            "text": system_prompt,
            "cache_control": {"type": "ephemeral"},
        }
    ]


async def call_llm(
    system_prompt: str,
    user_message: str,
    trace_name: str,
    json_schema: dict | None = None,
    model: str | None = None,
    max_tokens: int = 1024,
    temperature: float = 0.0,
    max_retries: int = 1,
) -> LLMResult:
    """
    The single shared function every LLM-calling component uses.
    See llm_client history in-conversation for the full walkthrough
    of json_schema (forced structured output), temperature defaults,
    and retry behavior. system_prompt is automatically wrapped for
    prompt caching - callers don't need to do anything differently.
    """
    resolved_model = model or DEFAULT_MODEL
    messages = [{"role": "user", "content": user_message}]
    system_block = _cached_system_block(system_prompt)

    with trace(name=trace_name, run_type="chain") as run:
        attempt = 0
        while True:
            try:
                if json_schema:
                    response = await client.messages.create(
                        model=resolved_model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_block,
                        messages=messages,
                        tools=[_build_forced_json_tool(json_schema)],
                        tool_choice={"type": "tool", "name": _TOOL_NAME},
                    )
                    tool_block = next(
                        (b for b in response.content if b.type == "tool_use"), None
                    )
                    if tool_block is None:
                        raise ValueError("no tool_use block in response")
                    result: dict | str = tool_block.input
                else:
                    response = await client.messages.create(
                        model=resolved_model,
                        max_tokens=max_tokens,
                        temperature=temperature,
                        system=system_block,
                        messages=messages,
                    )
                    result = response.content[0].text

                usage = response.usage
                run.end(outputs={"result_type": type(result).__name__})
                return LLMResult(
                    content=result,
                    input_tokens=usage.input_tokens,
                    output_tokens=usage.output_tokens,
                    cache_creation_input_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
                    cache_read_input_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
                )

            except (ValueError, json.JSONDecodeError) as e:
                if attempt < max_retries:
                    attempt += 1
                    messages.append(
                        {"role": "user", "content": "Your last response was invalid. Try again, following the required format exactly."}
                    )
                    continue

                safe_preview = str(e)[:200]
                run.end(error=f"failed after {attempt + 1} attempt(s): {safe_preview}")
                raise ValueError(
                    f"[{trace_name}] failed after {attempt + 1} attempt(s): {safe_preview}"
                ) from e