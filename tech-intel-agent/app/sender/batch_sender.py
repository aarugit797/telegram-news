import asyncio
import uuid

from app.core.logging_config import get_logger
from app.core.channels import get_channel, send_message
from app.db.repository_conversation import get_active_users
from app.db.repository_news import update_batch_delivery
from app.db.session_conversation import get_conversation_session
from app.db.session_news import get_news_session
from app.queues.redis_client import (
    claim_batch_for_delivery,
    complete_batch_delivery,
    push_dead_letter,
    release_batch_delivery,
)

logger = get_logger(__name__)

# Gap between one USER's consecutive messages, so a 3-message batch
# arrives as 3 separate bubbles rather than one instant burst.
#
# Also satisfies Telegram's rate limit of roughly 1 message/second to the
# same chat (see channels/telegram.py). The spacing lives here rather
# than inside each channel because it is a product decision - how a batch
# should FEEL to read - that happens to also clear the technical floor.
MESSAGE_DELAY_SECONDS = 2


async def _send_batch_to_user(channel_user_id: str, messages: list[str]) -> bool:
    """Sends a user their full message sequence, spaced apart. Returns True only if every message succeeded."""
    for i, message_text in enumerate(messages):
        try:
            await send_message(user_number, message_text)
        except Exception as e:
            logger.error(
                "Failed to send message to user",
                extra={"extra_fields": {"user": channel_user_id, "error": str(e)}},
            )
            return False
        if i < len(messages) - 1:
            await asyncio.sleep(MESSAGE_DELAY_SECONDS)
    return True


async def run_sender_worker() -> None:
    """
    Drains one batch from the delivery queue and fans it out to every
    active user, then reports delivery status back to the News DB.

    At our test-user scale (10-20 users) sending one user at a time is
    simple and fast enough. At real scale this would need to send many
    users concurrently (e.g. an asyncio.Semaphore capping concurrency)
    rather than sequentially - flagged as the known next scaling step,
    not implemented in v1.
    """
    # CLAIMED, not popped. The batch moves to an in-flight list and is
    # only destroyed once delivery has actually succeeded.
    #
    # This used to call pop_batch(), which removed the payload before
    # anything was sent. Its signals are already flagged is_sent by the
    # batching agent, so a failed send left the messages gone from Redis
    # AND the signals unreturnable by get_unsent_signals - permanently
    # marked delivered with nothing delivered, and nothing to re-batch
    # them. Observed live four times against a Twilio 400; each batch was
    # only recovered because its payload had been dumped to a file first.
    #
    # This is the same defect fixed one stage upstream in the batching
    # agent (flag the DB last). The pipeline claims at-least-once
    # delivery; popping first made this stage at-most-once, silently.
    claimed = await claim_batch_for_delivery()
    if not claimed:
        return

    batch_data, raw_payload = claimed
    batch_id_str = batch_data["batch_id"]
    messages = batch_data["messages"]

    async with get_conversation_session() as conv_session:
        users = await get_active_users(conv_session)

    delivered_count = 0
    for user in users:
        success = await _send_batch_to_user(user.channel_user_id, messages)
        if success:
            delivered_count += 1
        else:
            await push_dead_letter({
                "component": "batch_sender",
                "batch_id": batch_id_str,
                "user": user.channel_user_id,
                "error": "delivery failed",
            })

    if delivered_count == len(users) and users:
        delivery_status = "delivered"
    elif delivered_count > 0:
        delivery_status = "partial"
    else:
        delivery_status = "failed"

    if delivery_status == "failed":
        # Nobody got it - put the batch back so a later run retries it,
        # rather than dropping it on the floor.
        await release_batch_delivery(raw_payload)
    else:
        await complete_batch_delivery(raw_payload)

    async with get_news_session() as news_session:
        await update_batch_delivery(news_session, uuid.UUID(batch_id_str), delivered_count, delivery_status)

    logger.info(
        "Sender worker run complete",
        extra={"extra_fields": {
            "batch_id": batch_id_str, "total_users": len(users),
            "delivered": delivered_count, "status": delivery_status,
            "channel": get_channel().name,
        }},
    )
