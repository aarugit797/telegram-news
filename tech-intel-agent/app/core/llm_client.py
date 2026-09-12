import json
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import types
from langsmith import trace

from app.core.config import settings

DEFAULT_MODEL = settings.default_llm_model


@dataclass
class LLMResult:
    """
    Provider-neutral result. Every provider adapter normalises into this
    shape, so the 13 call sites never learn which provider answered.

    Token field names differ per provider and are mapped in the adapter:
    Anthropic reported usage.input_tokens / output_tokens, Gemini
    reports usage_metadata.prompt_token_count / candidates_token_count.

    cache_creation_input_tokens and cache_read_input_tokens are retained
    at 0 defaults. They were Anthropic prompt-caching counters, and no
    caching is implemented for Gemini (see GeminiProvider), so nothing
    sets them today. They stay because tests construct LLMResult with
    them, and because a provider that DOES report cache usage should
    report it here rather than inventing a parallel field.
    """
    content: dict | str
    input_tokens: int
    output_tokens: int
    provider: str = ""
    model: str = ""
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


class LLMProvider(ABC):
    """
    One method, because that is genuinely all this system asks of a
    model. Adding a second provider means implementing this once - no
    call site changes, since everything goes through call_llm below.
    """

    name: str

    @abstractmethod
    async def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        json_schema: dict | None,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResult:
        ...


# NOTE ON SCHEMA SUPPORT - measured, not assumed.
#
# Gemini's structured output does support the keywords our filter
# schemas rely on. Sending GITHUB_FILTER_JSON_SCHEMA unmodified, and
# each keyword in isolation, all succeeded against gemini-3.5-flash:
# minimum/maximum, exclusiveMinimum/exclusiveMaximum,
# additionalProperties and enum were every one accepted.
#
# So the schema is passed through UNCHANGED. An earlier version of this
# adapter stripped minimum/maximum defensively, and that was actively
# harmful: with the bounds removed the model answered relevance=8 and
# applicability=8 on a 1-5 scale on the first attempt. The bounds are
# doing real work in the prompt, and removing them silently widened the
# range the model felt free to use.
#
# _validate_schema_bounds below stays anyway, as defence in depth - the
# schema constrains what the model is ASKED for, and this constrains
# what we are willing to ACCEPT.


def _validate_schema_bounds(payload: dict, schema: dict) -> None:
    """
    Checks the returned value against the same constraints the schema
    declares. Not redundant: structured output shapes the response, it
    does not guarantee it, and an out-of-range score flows straight into
    hybrid_filter's composite average where it would skew every
    threshold comparison downstream, silently and permanently.

    Raises ValueError so call_llm's existing retry path treats it
    exactly like malformed JSON - one corrective retry, then a hard
    failure, rather than a bad score reaching the database.
    """
    properties = schema.get("properties", {})

    for key in schema.get("required", []):
        if key not in payload:
            raise ValueError(f"missing required key {key!r}")

    for key, rules in properties.items():
        if key not in payload:
            continue
        value = payload[key]

        expected = rules.get("type")
        if expected == "integer" and not isinstance(value, int):
            raise ValueError(f"{key!r} must be an integer, got {type(value).__name__}")
        if expected == "string" and not isinstance(value, str):
            raise ValueError(f"{key!r} must be a string, got {type(value).__name__}")

        low, high = rules.get("minimum"), rules.get("maximum")
        if low is not None and value < low:
            raise ValueError(f"{key!r}={value} is below minimum {low}")
        if high is not None and value > high:
            raise ValueError(f"{key!r}={value} is above maximum {high}")


class GeminiProvider(LLMProvider):
    """
    Google AI Studio's free tier - no card required, which is the reason
    this replaced Anthropic.

    STRUCTURED OUTPUT: where the Anthropic path forced a tool call and
    read the tool's input block, Gemini takes
    response_mime_type="application/json" plus a schema and returns the
    JSON as ordinary response text.

    PROMPT CACHING: not implemented. The Anthropic path marked the
    system prompt with cache_control, which mattered because every
    filter sends an identical system prompt hundreds of times a day.
    Gemini's equivalent is explicit cached-content objects with their
    own creation and TTL lifecycle - a different enough mechanism that
    it is left out rather than half-emulated. Caching is a per-provider
    concern and is currently unimplemented for this provider.
    """

    name = "gemini"

    def __init__(self) -> None:
        self._client = genai.Client(api_key=settings.gemini_api_key)

    async def complete(
        self,
        *,
        system_prompt: str,
        user_message: str,
        json_schema: dict | None,
        model: str,
        max_tokens: int,
        temperature: float,
    ) -> LLMResult:
        config: dict[str, Any] = {
            "system_instruction": system_prompt,
            "temperature": temperature,
            "max_output_tokens": max_tokens,
        }

        if json_schema:
            config["response_mime_type"] = "application/json"
            # Passed through as-is - see the schema-support note above.
            config["response_json_schema"] = json_schema

        response = await self._client.aio.models.generate_content(
            model=model,
            contents=user_message,
            config=types.GenerateContentConfig(**config),
        )

        text = response.text
        if not text:
            raise ValueError("empty response from Gemini")

        if json_schema:
            content: dict | str = json.loads(text)
            if not isinstance(content, dict):
                raise ValueError(f"expected a JSON object, got {type(content).__name__}")
            _validate_schema_bounds(content, json_schema)
        else:
            content = text

        # Gemini's usage counters are named differently from Anthropic's.
        # This mapping is the only place in the system that knows that.
        usage = response.usage_metadata
        return LLMResult(
            content=content,
            input_tokens=getattr(usage, "prompt_token_count", 0) or 0,
            output_tokens=getattr(usage, "candidates_token_count", 0) or 0,
            provider=self.name,
            model=model,
        )


_provider: LLMProvider = GeminiProvider()


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
    The single shared function every LLM-calling component uses. Its
    signature is deliberately unchanged by the move from Anthropic to
    Gemini - routing all 13 call sites through here is what made that
    swap a one-file change.

    LANGSMITH: the old path relied on wrap_anthropic, which instrumented
    the SDK client automatically. There is no equivalent wrapper for the
    Gemini SDK, so the named span is kept and the request and response
    are written into it by hand below. Without that the trace would
    record only that a call happened, not what was sent or returned.
    """
    resolved_model = model or DEFAULT_MODEL
    effective_user_message = user_message

    with trace(
        name=trace_name,
        run_type="llm",
        inputs={
            "provider": _provider.name,
            "model": resolved_model,
            "system_prompt": system_prompt,
            "user_message": user_message,
            "temperature": temperature,
            "structured": bool(json_schema),
        },
    ) as run:
        attempt = 0
        while True:
            try:
                result = await _provider.complete(
                    system_prompt=system_prompt,
                    user_message=effective_user_message,
                    json_schema=json_schema,
                    model=resolved_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
                run.end(
                    outputs={
                        "content": result.content,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "attempts": attempt + 1,
                    }
                )
                return result

            except (ValueError, json.JSONDecodeError) as e:
                if attempt < max_retries:
                    attempt += 1
                    # Gemini has no assistant turn to append a correction
                    # after the way the Anthropic path did, so the
                    # correction is folded into the user turn instead.
                    effective_user_message = (
                        f"{user_message}\n\n"
                        "Your last response was invalid. Respond again, following "
                        "the required format exactly."
                    )
                    continue

                safe_preview = str(e)[:200]
                run.end(error=f"failed after {attempt + 1} attempt(s): {safe_preview}")
                raise ValueError(
                    f"[{trace_name}] failed after {attempt + 1} attempt(s): {safe_preview}"
                ) from e
