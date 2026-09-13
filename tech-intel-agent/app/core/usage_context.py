"""
Per-request LLM usage accounting.

WHY A CONTEXTVAR AND NOT A MODULE-LEVEL GLOBAL.

The responder handles several users' messages concurrently on one event
loop - every inbound WhatsApp message becomes a FastAPI background task,
and those tasks interleave at every await. A module-level counter would
be shared by all of them, so one user's guardrail call would be added to
whichever user's total happened to be mid-flight. Both users' numbers
would be wrong, and the error would grow with traffic - worst exactly
when the cap matters most.

A ContextVar is scoped to the async task that set it. asyncio copies the
context when a task is created, so a task started inside _process_message
inherits the same accumulator, while a different user's task carries its
own.

The stored value is a MUTABLE dict that is mutated in place rather than
re-set. Context copies share the dict object, so a nested task's
record_call is visible to the parent that later reads get_totals(). A
`.set()` on each call would write into the child's copy only, and the
parent would read zero.
"""
from contextvars import ContextVar, Token

# None means "no tracking context active" - the pipeline's agents never
# start one, and must keep working untouched.
_usage: ContextVar[dict | None] = ContextVar("llm_usage", default=None)


def start_tracking() -> Token:
    """
    Begins accounting for the current task. Called once at the top of
    _process_message, before any LLM call in the chain.

    Returns a Token that MUST be handed to stop_tracking() when the
    message is done. Without that reset the value outlives the call:
    _process_message is awaited inline rather than spawned as its own
    Task (Starlette runs background tasks sequentially in one task), so
    a .set() here persists in the caller's context after the function
    returns, and the next thing to run in that task would be counted
    against the previous user.
    """
    return _usage.set({"calls": 0, "tokens": 0})


def stop_tracking(token: Token) -> None:
    """
    Ends accounting and restores whatever was in scope before - normally
    None. Pair this with start_tracking() in a finally block so it runs
    on every exit path, including an exception mid-chain.
    """
    _usage.reset(token)


def record_call(tokens: int) -> None:
    """
    Records one LLM call and its token count.

    A NO-OP when no context is active. This is what lets the same
    choke point in llm_client serve both callers: the responder starts a
    context and gets per-user numbers, while the pipeline's five agents
    never start one and are unaffected. Their calls have no user to
    attribute usage to, so silently dropping them is correct rather than
    a gap.
    """
    state = _usage.get()
    if state is None:
        return
    state["calls"] += 1
    state["tokens"] += max(int(tokens), 0)


def get_totals() -> tuple[int, int]:
    """(calls, tokens) accumulated in this context. (0, 0) if untracked."""
    state = _usage.get()
    if state is None:
        return 0, 0
    return state["calls"], state["tokens"]
