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
from app.responder.conversational_agent import run_conversational_agent
from app.responder.cost_tracker import is_under_cost_limit, record_llm_usage
from app.responder.guardrail import check_guardrail
from app.responder.intent_classifier import classify_intent
from app.responder.rate_limit import check_and_increment_rate_limit
from app.responder.response_composer import compose_final_response
from app.responder.token_budget import get_conversation_context
from app.responder.whitelist import check_whitelist, ensure_user_record

logger = get_logger(__name__)

FIXED_RESPONSES = {
    "not_whitelisted": "This service is currently invite-only. Contact us to join the waitlist.",
    "rate_limited": "You've reached today's message limit. Try again tomorrow.",
    "cost_limited": "You've reached today's usage limit. Try again tomorrow.",
    "injection_detected": (
        "I noticed that message was trying to change how I work. "
        "I'm your tech intelligence agent and I stay focused on tech news and insights."
    ),
    "off_topic": "I'm focused on tech news and insights. Ask me about what's happening in AI, engineering, or any article I've sent you.",
}


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
            await send_message(from_number, FIXED_RESPONSES["not_whitelisted"])
            return

        user = await ensure_user_record(from_number)

        if not await check_and_increment_rate_limit(str(user.id)):
            await send_message(from_number, FIXED_RESPONSES["rate_limited"])
            return

        if not await is_under_cost_limit(user.id):
            await send_message(from_number, FIXED_RESPONSES["cost_limited"])
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
            await send_message(from_number, FIXED_RESPONSES["injection_detected"])
            return
        if guardrail_result == "OFF_TOPIC":
            await send_message(from_number, FIXED_RESPONSES["off_topic"])
            return

        intent = await classify_intent(message_body)

        # NOTE - conversation context IS computed here (compressed
        # summary + recent raw history) but no current tool actually
        # consumes it - each tool only takes the current message. This is
        # a known v1 simplification, not a silent oversight: multi-turn
        # context awareness within a tool call is a real gap worth
        # closing before this handles genuinely long conversations well.
        #
        # Its history-summarizer LLM call runs inside this function, so
        # it IS captured by the accounting above.
        _context = await get_conversation_context(user.id)

        agent_result = await run_conversational_agent(intent, message_body, str(user.id))
        final_reply = await compose_final_response(agent_result["output"])

        await send_message(from_number, final_reply)

        async with get_conversation_session() as session:
            await save_message(session, {
                "user_id": user.id, "direction": "outbound", "message_text": final_reply,
                "intent_classification": intent, "guardrail_result": guardrail_result,
            })

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
