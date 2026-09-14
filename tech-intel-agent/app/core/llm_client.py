import asyncio
import json
import random
import re
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Literal

from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from langsmith import trace

from groq import AsyncGroq
from groq import _exceptions as groq_errors

from app.core.config import settings
from app.core.logging_config import get_logger
from app.core.usage_context import record_call
from app.queues.redis_client import (
    llm_cooldown_remaining,
    llm_daily_used,
    llm_slots_used,
    mark_llm_cooldown,
    try_acquire_llm_daily,
    try_acquire_llm_slot,
)

logger = get_logger(__name__)

DEFAULT_MODEL = settings.default_llm_model

Lane = Literal["pipeline", "responder"]


class LLMQuotaExhausted(Exception):
    """
    Raised when a call cannot be made because the shared budget is spent -
    not because the call failed.

    Distinct from ValueError deliberately. An agent catching this knows
    the item was NEVER JUDGED, so it must not be dead-lettered and must
    not be cached as rejected: both would permanently record a verdict
    that was never reached. The item simply gets picked up next run.
    """


# The Gemini SDK's exception classes, confirmed by reading
# google/genai/errors.py rather than assumed: raise_error() maps
# 400-499 to ClientError and 500-599 to ServerError, both carrying a
# numeric .code and a string .status.
#
# Catching ClientError wholesale would be wrong - it also covers 400
# INVALID_ARGUMENT, which will fail identically on every retry. Only the
# codes below are genuinely transient.
_RETRYABLE_STATUS_CODES = frozenset({429, 500, 502, 503, 504})

# Extra tokens granted to Groq reasoning models on top of whatever the
# caller asked for, since reasoning is billed against the same budget
# but never appears in the response.
GROQ_REASONING_HEADROOM_TOKENS = 2048

# Both SDKs' error hierarchies, so one except clause covers the pool.
_PROVIDER_ERRORS = (genai_errors.APIError, groq_errors.APIStatusError)


def _status_code(exc: BaseException) -> int | None:
    """
    The HTTP status, whichever SDK raised it. Gemini's APIError exposes
    `.code`; Groq follows the OpenAI SDK shape and exposes
    `.status_code`. Normalising here keeps the retry logic
    provider-neutral.
    """
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    status = getattr(exc, "status_code", None)
    return status if isinstance(status, int) else None


def _is_transient(exc: BaseException) -> bool:
    return (
        isinstance(exc, _PROVIDER_ERRORS)
        and _status_code(exc) in _RETRYABLE_STATUS_CODES
    )


def _is_rate_limit(exc: BaseException) -> bool:
    """
    429 specifically. Distinguished from other transient failures because
    the response is different: a 429 means THIS CREDENTIAL is spent, so
    it gets parked and the call rotates to another. A 503 means the
    SERVICE is busy, so backing off and retrying the same credential is
    right - rotating would spend a second credential's quota on what is
    not a quota problem.
    """
    return isinstance(exc, _PROVIDER_ERRORS) and _status_code(exc) == 429


def _retry_after_seconds(exc: BaseException) -> float | None:
    """
    Google returns a RetryInfo hint on 429 telling us how long it
    actually wants us to wait. Preferred over computed backoff whenever
    present - the server knows when the window resets and we are
    guessing.

    Two shapes are checked: the structured RetryInfo entry in
    error.details, and a Retry-After response header.
    """
    details = getattr(exc, "details", None)
    if isinstance(details, dict):
        entries = details.get("error", {}).get("details", [])
        if isinstance(entries, list):
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if "RetryInfo" in str(entry.get("@type", "")):
                    delay = str(entry.get("retryDelay", ""))
                    match = re.match(r"^([\d.]+)s?$", delay)
                    if match:
                        return float(match.group(1))

    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers:
        raw = headers.get("Retry-After") or headers.get("retry-after")
        if raw:
            try:
                return float(raw)
            except (TypeError, ValueError):
                pass
    return None


def _backoff_seconds(attempt: int) -> float:
    """
    Exponential backoff with full jitter. The jitter is not cosmetic: at
    a 10 RPM ceiling, five agents retrying on an identical schedule would
    re-collide on every wave and convert one 429 into a synchronised
    stampede.
    """
    ceiling = min(
        settings.llm_backoff_base_seconds * (2 ** attempt),
        settings.llm_backoff_max_seconds,
    )
    return random.uniform(0, ceiling)


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


def _harden_for_strict(schema: Any) -> Any:
    """
    Groq's strict json_schema mode is stricter than OpenAI's in a way the
    docs gloss over: it rejects any object lacking
    `additionalProperties: false`, with

        "`additionalProperties:false` must be set on every object"

    Confirmed against the live API, not assumed. Our 10 schemas all list
    every property under `required` already - that part was fine - but
    none set additionalProperties, so every one of them is rejected
    unmodified.

    Applied HERE rather than by editing the shared schema files, because
    this is one provider's dialect. Gemini accepts the schemas as they
    are, and rewriting them to suit Groq would push a Groq-shaped
    constraint onto the prompts every provider reads.
    """
    if isinstance(schema, dict):
        out = {k: _harden_for_strict(v) for k, v in schema.items()}
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out.setdefault("required", sorted(out.get("properties", {})))
        return out
    if isinstance(schema, list):
        return [_harden_for_strict(v) for v in schema]
    return schema


class GroqProvider(LLMProvider):
    """
    Overflow only. Groq's RPM is generous but a 100K tokens-per-day cap
    means roughly 90 calls/day at our prompt sizes, so the pool prefers
    every Gemini credential before reaching this.

    OpenAI-compatible wire format: a system/user message pair and
    response_format={"type": "json_schema", ...} instead of Gemini's
    system_instruction plus response_json_schema.
    """

    name = "groq"

    def __init__(self) -> None:
        self._client = AsyncGroq(api_key=settings.groq_api_key)

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
        # REASONING HEADROOM. Groq's gpt-oss models reason before
        # answering, and those reasoning tokens are billed against the
        # SAME max_tokens as the visible answer - while never appearing
        # in the content. Callers size max_tokens for the answer they
        # want ("300 characters of reply"), so passing it through
        # unchanged silently starves the answer.
        #
        # Measured on identical calls: reasoning ran 613, 845 and 1067
        # characters on three consecutive requests. At max_tokens=300
        # that is sometimes enough and sometimes not, which is exactly
        # how it presented - a response-composer call that failed once
        # with "empty response from Groq" and succeeded on retry.
        #
        # The adapter therefore translates the caller's intent into the
        # provider's accounting, which is the job of an adapter.
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens + GROQ_REASONING_HEADROOM_TOKENS,
        }
        if json_schema:
            kwargs["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": "structured_output",
                    "strict": True,
                    "schema": _harden_for_strict(json_schema),
                },
            }

        response = await self._client.chat.completions.create(**kwargs)
        choice = response.choices[0]
        text = choice.message.content
        if not text:
            # Name the actual cause. "empty response" alone sent an
            # earlier investigation looking for a network fault when the
            # answer was that reasoning had consumed the token budget.
            raise ValueError(
                f"empty response from Groq (finish_reason={choice.finish_reason!r}, "
                f"completion_tokens={getattr(response.usage, 'completion_tokens', '?')}) - "
                f"reasoning likely consumed the token budget"
            )

        if json_schema:
            content: dict | str = json.loads(text)
            if not isinstance(content, dict):
                raise ValueError(f"expected a JSON object, got {type(content).__name__}")
        else:
            content = text

        # OpenAI-shaped counters, where Gemini used
        # prompt_token_count / candidates_token_count.
        usage = response.usage
        return LLMResult(
            content=content,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            provider=self.name,
            model=model,
        )


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

    def __init__(self, api_key: str) -> None:
        # One client per credential. The key is held here and nowhere
        # else - it is never placed on the Credential's repr, in a log
        # field, or in a trace payload.
        self._client = genai.Client(api_key=api_key)

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
            # Bounds are NOT checked here. Validating the returned values
            # is provider-neutral policy, so it lives in call_llm - a
            # second adapter cannot forget to apply it.
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


@dataclass
class Credential:
    """
    One usable set of quota. `credential_id` is a short stable label
    ("gemini-2") used for Redis keys, log fields and trace payloads - the
    API key itself lives inside the provider object and is never exposed
    through this dataclass.

    `rank` orders selection: 0 for Gemini, 1 for Groq. Groq is overflow
    rather than a peer because its 100K tokens-per-day cap works out
    around 90 calls/day at our prompt sizes.
    """
    credential_id: str
    provider: LLMProvider
    model: str
    rpm_limit: int
    rpd_limit: int
    rank: int


def _build_pool() -> list[Credential]:
    pool: list[Credential] = []

    for index, key in enumerate(settings.gemini_keys(), start=1):
        pool.append(Credential(
            credential_id=f"gemini-{index}",
            provider=GeminiProvider(key),
            model=settings.default_llm_model,
            rpm_limit=settings.gemini_rpm_per_key,
            rpd_limit=settings.gemini_rpd_per_key,
            rank=0,
        ))

    if settings.groq_api_key:
        pool.append(Credential(
            credential_id="groq-1",
            provider=GroqProvider(),
            model=settings.groq_model,
            rpm_limit=settings.groq_rpm,
            rpd_limit=settings.groq_rpd,
            rank=1,
        ))

    if not pool:
        raise RuntimeError(
            "No LLM credentials configured - set GEMINI_API_KEYS and/or GROQ_API_KEY"
        )
    return pool


_pool: list[Credential] = _build_pool()


def credential_ids() -> list[str]:
    """Public, key-free list of pool members. For tests and diagnostics."""
    return [c.credential_id for c in _pool]


async def _select_credential(lane: Lane) -> tuple[Credential, str] | tuple[None, str]:
    """
    Picks the credential that should serve this call, or explains why
    none can.

    Ordering is (rank, -headroom): Gemini before Groq, and within Gemini
    the key with the MOST remaining headroom. Deliberately not strict
    round-robin - round-robin keeps offering work to a credential that is
    already at its ceiling, so every Nth call pays a wasted 429 before
    moving on.

    Credentials in cooldown are skipped here rather than attempted and
    failed. That is the whole point of the cooldown state: it prevents
    rotating straight back into a key that has just said it is spent.
    """
    skipped: list[str] = []
    candidates: list[tuple[int, int, Credential]] = []

    for cred in _pool:
        cooling = await llm_cooldown_remaining(cred.credential_id)
        if cooling:
            skipped.append(f"{cred.credential_id}(cooldown {cooling}s)")
            continue

        rpd_limit = (
            cred.rpd_limit if lane == "responder"
            else int(cred.rpd_limit * settings.llm_rpd_pipeline_fraction)
        )
        if await llm_daily_used(cred.credential_id, settings.scheduler_timezone) >= rpd_limit:
            skipped.append(f"{cred.credential_id}(daily spent)")
            continue

        rpm_limit = (
            cred.rpm_limit if lane == "responder"
            else max(cred.rpm_limit - settings.llm_rpm_responder_reserved, 1)
        )
        used = await llm_slots_used(cred.credential_id)
        headroom = rpm_limit - used
        if headroom <= 0:
            skipped.append(f"{cred.credential_id}(rpm full)")
            continue

        candidates.append((cred.rank, -headroom, cred))

    if not candidates:
        return None, ", ".join(skipped) or "no credentials configured"

    candidates.sort(key=lambda t: (t[0], t[1]))
    return candidates[0][2], ", ".join(skipped)


async def _reserve(cred: Credential, lane: Lane) -> bool:
    """
    Takes one RPM slot and one daily request on this credential.

    ORDER MATTERS, and it is RPM first. Neither acquisition can be undone
    - both are fire-and-forget INCRs in Redis - so whichever is taken
    first is LEAKED when the second fails. The question is only which
    resource can afford to leak.

    Taking the daily unit first (as this did originally) leaked the
    expensive one: RPM contention is routine and call_llm rotates on a
    failed reserve, so the same credential could be reselected up to
    max_rotations (len(pool) * 2 = 8) times, each attempt burning a
    daily unit for an API call that never happened. Under sustained
    contention that silently drains the RPD budget - the failure a user
    would only notice as the pipeline going quiet hours early.

    Taking the RPM slot first inverts which resource leaks: a slot lost
    to a failed daily acquisition recovers in 60 seconds on its own,
    where a daily unit does not recover until midnight. Fail fast on the
    cheap, fast-recovering resource before consuming the expensive,
    slow-recovering one.

    The residual leak is real but bounded and self-healing: it happens
    only when the daily budget is exhausted, which is exactly when no
    further calls should be made on this credential anyway.
    """
    rpm_limit = (
        cred.rpm_limit if lane == "responder"
        else max(cred.rpm_limit - settings.llm_rpm_responder_reserved, 1)
    )
    slot_ok, _ = await try_acquire_llm_slot(cred.credential_id, rpm_limit)
    if not slot_ok:
        return False

    rpd_limit = (
        cred.rpd_limit if lane == "responder"
        else int(cred.rpd_limit * settings.llm_rpd_pipeline_fraction)
    )
    daily_ok, _ = await try_acquire_llm_daily(
        cred.credential_id, rpd_limit, settings.scheduler_timezone
    )
    return daily_ok


async def call_llm(
    system_prompt: str,
    user_message: str,
    trace_name: str,
    json_schema: dict | None = None,
    model: str | None = None,
    # 4096, not 1024. This has now caused three separate production
    # failures - dedup, the message composer, and the filters - each
    # presenting as a JSON parse error ("Unterminated string") rather
    # than as "response too long", because truncation cuts the model off
    # mid-JSON and the caller sees malformed output.
    #
    # 1024 was a reasonable default for a plain completion API. It is not
    # one here: on gemini-3.5-flash and Groq's gpt-oss models, reasoning
    # tokens are billed against this same budget while never appearing in
    # the response, so the VISIBLE output is cut far below the nominal
    # cap. A caller asking for "1024 tokens of answer" silently gets
    # whatever is left after the model finishes thinking.
    #
    # Raising the default fixes the seven call sites that never set it,
    # and - more usefully - stops the next call site inheriting the
    # problem. Callers that genuinely need a tighter ceiling still pass
    # one explicitly.
    max_tokens: int = 4096,
    temperature: float = 0.0,
    max_retries: int = 1,
    lane: Lane = "pipeline",
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

    RATE LIMITING: a slot in the shared cross-process budget is taken
    before each provider attempt - including retries, since a retry is
    another real request against the same ceiling. `lane` decides which
    ceiling applies and defaults to "pipeline", so background work can
    never consume the capacity held back for a waiting user.

    TWO RETRY BUDGETS, deliberately separate:
      - format_attempt  covers malformed JSON / failed validation, and
        is bounded by max_retries. Retrying asks the model to try again.
      - transient_attempt covers 429 and 503, and is bounded by
        llm_max_transient_retries. Retrying just waits.
    They are different failure classes, so sharing one counter would let
    a burst of 429s consume the budget that exists for correcting a
    malformed response, and vice versa.
    """
    # `model` overrides the credential's own model when a caller pins one;
    # otherwise each credential brings its own (Gemini keys share
    # default_llm_model, Groq uses groq_model).
    pinned_model = model
    effective_user_message = user_message

    with trace(
        name=trace_name,
        run_type="llm",
        inputs={
            "lane": lane,
            "pool": credential_ids(),
            "system_prompt": system_prompt,
            "user_message": user_message,
            "temperature": temperature,
            "structured": bool(json_schema),
        },
    ) as run:
        format_attempt = 0
        transient_attempt = 0
        rotations = 0
        # Bounded so a pool where every credential 429s cannot spin: each
        # credential may be tried at most twice before we give up.
        max_rotations = len(_pool) * 2
        tried: list[str] = []

        while True:
            cred, skipped = await _select_credential(lane)

            if cred is None:
                message = (
                    f"[{trace_name}] every credential unavailable for lane={lane}: {skipped}"
                )
                logger.warning(
                    "LLM pool exhausted",
                    extra={"extra_fields": {
                        "trace_name": trace_name, "lane": lane,
                        "skipped": skipped, "tried": tried,
                    }},
                )
                run.end(error=message)
                raise LLMQuotaExhausted(message)

            if rotations > max_rotations:
                message = f"[{trace_name}] rotation limit reached after trying {tried}"
                run.end(error=message)
                raise LLMQuotaExhausted(message)

            if not await _reserve(cred, lane):
                # Another process took the last slot between selection and
                # reservation. Not an error - reselect.
                rotations += 1
                continue

            resolved_model = pinned_model or cred.model
            tried.append(cred.credential_id)

            try:
                result = await cred.provider.complete(
                    system_prompt=system_prompt,
                    user_message=effective_user_message,
                    json_schema=json_schema,
                    model=resolved_model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )

                # Validated HERE rather than inside the adapter: the check
                # is provider-neutral, so applying it at this level means a
                # future adapter cannot forget it. Raises ValueError, which
                # the format-retry branch below handles.
                if json_schema and isinstance(result.content, dict):
                    _validate_schema_bounds(result.content, json_schema)

                # The single choke point for usage accounting. Every LLM
                # call in the system passes through here, so no call site
                # can silently forget to report - which is how the old
                # record_llm_cost ended up with zero callers.
                #
                # No-op unless a tracking context is active, so the
                # pipeline's agents are unaffected.
                record_call(result.input_tokens + result.output_tokens)

                logger.info(
                    "LLM call served",
                    extra={"extra_fields": {
                        "trace_name": trace_name,
                        "lane": lane,
                        # Credential ID only - never the key itself.
                        "credential_id": cred.credential_id,
                        "provider": cred.provider.name,
                        "model": resolved_model,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "rotations": rotations,
                    }},
                )
                run.end(
                    outputs={
                        "content": result.content,
                        "credential_id": cred.credential_id,
                        "provider": cred.provider.name,
                        "model": resolved_model,
                        "input_tokens": result.input_tokens,
                        "output_tokens": result.output_tokens,
                        "format_attempts": format_attempt + 1,
                        "transient_attempts": transient_attempt,
                        "rotations": rotations,
                    }
                )
                return result

            except LLMQuotaExhausted:
                raise

            except _PROVIDER_ERRORS as e:
                code = _status_code(e)

                if _is_rate_limit(e):
                    # THIS credential is spent - park it and rotate.
                    # Cooling it down is what stops selection handing the
                    # next call straight back to the same exhausted key.
                    hinted = _retry_after_seconds(e)
                    cooldown = hinted if hinted is not None else settings.llm_cooldown_seconds
                    await mark_llm_cooldown(cred.credential_id, cooldown)
                    logger.warning(
                        "Credential rate limited - cooling down and rotating",
                        extra={"extra_fields": {
                            "trace_name": trace_name,
                            "credential_id": cred.credential_id,
                            "provider": cred.provider.name,
                            "cooldown_seconds": round(float(cooldown), 1),
                            "source": "server hint" if hinted is not None else "configured default",
                        }},
                    )
                    rotations += 1
                    continue

                if not _is_transient(e):
                    run.end(error=f"non-retryable API error ({code}): {str(e)[:180]}")
                    raise

                # 5xx: the SERVICE is busy, not this credential's quota.
                # Back off and retry rather than rotating - rotating would
                # spend another credential on a non-quota problem.
                if transient_attempt >= settings.llm_max_transient_retries:
                    message = (
                        f"[{trace_name}] transient failures exhausted after "
                        f"{transient_attempt} retries on {cred.credential_id} ({code})"
                    )
                    run.end(error=message)
                    raise LLMQuotaExhausted(message) from e

                hinted = _retry_after_seconds(e)
                delay = hinted if hinted is not None else _backoff_seconds(transient_attempt)
                logger.warning(
                    "Transient LLM failure, backing off",
                    extra={"extra_fields": {
                        "trace_name": trace_name,
                        "credential_id": cred.credential_id,
                        "code": code,
                        "retry_attempt": transient_attempt + 1,
                        "sleep_seconds": round(delay, 2),
                        "source": "server hint" if hinted is not None else "computed backoff",
                    }},
                )
                transient_attempt += 1
                await asyncio.sleep(delay)
                continue

            except (ValueError, json.JSONDecodeError) as e:
                if format_attempt < max_retries:
                    format_attempt += 1
                    # Gemini has no assistant turn to append a correction
                    # after the way the Anthropic path did, so the
                    # correction is folded into the user turn instead.
                    effective_user_message = (
                        user_message
                        + "\n\nYour last response was invalid. Respond again, "
                        + "following the required format exactly."
                    )
                    continue

                safe_preview = str(e)[:200]
                run.end(error=f"failed after {format_attempt + 1} attempt(s): {safe_preview}")
                raise ValueError(
                    f"[{trace_name}] failed after {format_attempt + 1} attempt(s): {safe_preview}"
                ) from e
