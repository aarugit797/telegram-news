"""
The conversation chain for one inbound message.

Lives here rather than in a transport module because it is transport-
agnostic: the Telegram poller calls it today, and a Telegram webhook
route would call the same function unchanged once this is deployed
behind HTTPS. It was extracted from responder/webhook.py when Twilio was
removed - the Twilio route and its signature verification went, this did
not.
"""
from app.core.channels import send_message
from app.core.logging_config import get_logger
from app.core.usage_context import get_totals, start_tracking, stop_tracking
from app.db.repository_conversation import save_message
from app.db.session_conversation import get_conversation_session
from app.core.llm_client import LLMQuotaExhausted
from app.prompts.responder.fixed_messages import (
    BLOCKED_COST_LIMITED,
    BLOCKED_INJECTION,
    BLOCKED_NOT_WHITELISTED,
    BLOCKED_OFF_TOPIC,
    BLOCKED_RATE_LIMITED,
    CAPACITY_EXHAUSTED,
    UNEXPECTED_ERROR,
)
from app.responder.conversational_agent import run_conversational_agent
from app.responder.cost_tracker import is_under_cost_limit, record_llm_usage
from app.responder.guardrail import check_guardrail
from app.responder.intent_classifier import classify_intent
from app.responder.rate_limit import check_and_increment_rate_limit
from app.responder.response_composer import compose_final_response
from app.responder.token_budget import get_conversation_context, get_recent_turns
from app.responder.whitelist import check_whitelist, ensure_user_record

logger = get_logger(__name__)

async def process_message(from_number: str, message_body: str) -> None:
    """
    The full processing chain - runs as a background task AFTER
    Twilio already received its 200 OK, since Twilio times out
    webhook calls after ~5 seconds and this chain (multiple LLM
    calls) can't reliably fit inside that window.

    USAGE ACCOUNTING. start_tracking() opens a contextvar accumulator
    that every LLM call in the chain reports into via llm_client, and the
    finally block writes the total exactly once.

    The finally is load-bearing, not defensive. This function has several
    early returns, and two of them - INJECTION_DETECTED and OFF_TOPIC -
    happen AFTER the guardrail has already made an LLM call. Recording
    only on the success path would leave those calls unattributed, so a
    user could spend quota indefinitely by sending messages that fail the
    guardrail: each one costs a real request and none of them would count
    against their allowance. It also covers an exception mid-chain, where
    the calls made before the failure are just as real.
    """
    tracking_token = start_tracking()
    user = None

    try:
        if not await check_whitelist(from_number):
            await send_message(from_number, BLOCKED_NOT_WHITELISTED)
            return

        user = await ensure_user_record(from_number)

        if not await check_and_increment_rate_limit(str(user.id)):
            await send_message(from_number, BLOCKED_RATE_LIMITED)
            return

        if not await is_under_cost_limit(user.id):
            await send_message(from_number, BLOCKED_COST_LIMITED)
            return

        async with get_conversation_session() as session:
            await save_message(session, {
                "user_id": user.id, "direction": "inbound", "message_text": message_body,
            })

        guardrail_result = await check_guardrail(user.id, message_body)
        if guardrail_result == "INJECTION_DETECTED":
            # Neither the phone number nor the message text is logged.
            # LoggingIntegration turns a WARNING into a Sentry breadcrumb,
            # so this line was shipping a user's phone number and the
            # content of what they wrote to a third party - on the path
            # most likely to fire repeatedly for one user.
            #
            # The message itself is already in the messages table, keyed
            # by user_id, which is where it belongs and where access is
            # controlled. user_id is enough to correlate the two.
            logger.warning(
                "Injection attempt detected",
                extra={"extra_fields": {
                    "user_id": str(user.id),
                    "message_length": len(message_body),
                }},
            )
            await send_message(from_number, BLOCKED_INJECTION)
            return
        if guardrail_result == "OFF_TOPIC":
            await send_message(from_number, BLOCKED_OFF_TOPIC)
            return

        # TWO DIFFERENT VIEWS OF THE HISTORY, on purpose.
        #
        # The classifier gets the last few raw turns only. It is deciding
        # whether "is it useful for me?" contains a pronoun pointing at
        # the previous turn - a summary of last week cannot help with
        # that, and get_conversation_context can fire the summarizer LLM
        # call, which would put a second model in front of a routing
        # decision that has to be fast.
        #
        # The tools get the full compressed context, because they are the
        # ones that must actually resolve the referent and answer about
        # it.
        recent_turns = await get_recent_turns(user.id)
        intent = await classify_intent(message_body, recent_turns)

        # This used to be assigned to _context and never read, so every
        # tool saw the current message alone. The live failure: a reader
        # asked about a repo, then asked "is it useful for me?" and was
        # told about the bot's own features, because nothing in the chain
        # knew what "it" was.
        #
        # Its history-summarizer LLM call runs inside this function, so
        # it IS captured by the accounting above.
        context = await get_conversation_context(user.id)

        agent_result = await run_conversational_agent(
            intent, message_body, str(user.id), context
        )
        final_reply = await compose_final_response(agent_result["output"])

        await send_message(from_number, final_reply)

        async with get_conversation_session() as session:
            await save_message(session, {
                "user_id": user.id, "direction": "outbound", "message_text": final_reply,
                "intent_classification": intent, "guardrail_result": guardrail_result,
            })

    except LLMQuotaExhausted:
        # Every credential is cooling down or spent. The reader is owed
        # an answer either way - silence is indistinguishable from the
        # bot being switched off, and they will just send the message
        # again, which costs another guardrail call the pool also cannot
        # serve.
        logger.warning(
            "Reply abandoned - LLM pool exhausted",
            extra={"extra_fields": {"user_id": str(user.id) if user else None}},
        )
        await send_message(from_number, CAPACITY_EXHAUSTED)

    except Exception as e:
        # Deliberately broad. Anything reaching here is unplanned, and
        # the one thing that must not happen is the reader getting
        # nothing. Re-raised after telling them, so the poller still logs
        # it with the update id and it stays visible as a real failure
        # rather than being swallowed into a friendly message.
        logger.error(
            "Reply failed mid-chain",
            extra={"extra_fields": {
                "user_id": str(user.id) if user else None,
                "error": str(e), "error_type": type(e).__name__,
            }},
        )
        await send_message(from_number, UNEXPECTED_ERROR)
        raise

    finally:
        # One write for the whole chain - guardrail, intent classifier,
        # history summarizer, tool and composer - on every exit path.
        # `user` is None only when the whitelist check rejected the
        # number, in which case no LLM call was made and there is nobody
        # to attribute usage to.
        calls, tokens = get_totals()
        if user is not None and calls > 0:
            await record_llm_usage(user.id, calls, tokens)
            logger.info(
                "Message usage recorded",
                extra={"extra_fields": {
                    "user_id": str(user.id), "llm_calls": calls, "total_tokens": tokens,
                }},
            )

        # Restores the previous context, normally "untracked". Without
        # this the accumulator outlives the message - Starlette runs
        # background tasks sequentially in ONE task, so the next thing to
        # run would keep reporting into this user's totals.
        stop_tracking(tracking_token)
