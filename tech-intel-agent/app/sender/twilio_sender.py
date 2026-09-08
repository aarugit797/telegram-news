import asyncio
import uuid

from app.core.logging_config import get_logger
from app.core.twilio_client import send_whatsapp_message
from app.db.repository_conversation import get_active_users
from app.db.repository_news import update_batch_delivery
from app.db.session_conversation import get_conversation_session
from app.db.session_news import get_news_session
from app.queues.redis_client import push_dead_letter
from app.sender.delivery_queue import pop_batch

logger = get_logger(__name__)

# Gap between one USER's consecutive messages, so a 3-message batch
# arrives as 3 separate WhatsApp bubbles rather than one instant burst.
MESSAGE_DELAY_SECONDS = 2


async def _send_batch_to_user(user_number: str, messages: list[str]) -> bool:
    """Sends a user their full message sequence, spaced apart. Returns True only if every message succeeded."""
    for i, message_text in enumerate(messages):
        try:
            await send_whatsapp_message(user_number, message_text)
        except Exception as e:
            logger.error(
                "Failed to send message to user",
                extra={"extra_fields": {"user": user_number, "error": str(e)}},
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
    batch_data = await pop_batch()
    if not batch_data:
        return

    batch_id_str = batch_data["batch_id"]
    messages = batch_data["messages"]

    async with get_conversation_session() as conv_session:
        users = await get_active_users(conv_session)

    delivered_count = 0
    for user in users:
        success = await _send_batch_to_user(user.whatsapp_number, messages)
        if success:
            delivered_count += 1
        else:
            await push_dead_letter({
                "component": "twilio_sender",
                "batch_id": batch_id_str,
                "user": user.whatsapp_number,
                "error": "delivery failed",
            })

    if delivered_count == len(users) and users:
        delivery_status = "delivered"
    elif delivered_count > 0:
        delivery_status = "partial"
    else:
        delivery_status = "failed"

    async with get_news_session() as news_session:
        await update_batch_delivery(news_session, uuid.UUID(batch_id_str), delivered_count, delivery_status)

    logger.info(
        "Sender worker run complete",
        extra={"extra_fields": {
            "batch_id": batch_id_str, "total_users": len(users),
            "delivered": delivered_count, "status": delivery_status,
        }},
    )
