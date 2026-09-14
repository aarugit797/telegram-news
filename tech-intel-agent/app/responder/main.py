from fastapi import FastAPI

from app.core.observability import init_observability
from app.core.logging_config import get_logger
from app.responder.health import router as health_router

logger = get_logger(__name__)

init_observability(service_name="telegram-responder", include_fastapi=True)

# Serves /health only. The inbound message route is gone with Twilio -
# Telegram is consumed by responder/telegram_poller.py, which needs no
# HTTP server at all. This app stays because deployment platforms health
# check over HTTP, and because a Telegram webhook route would be added
# here once this runs behind public HTTPS.
app = FastAPI(title="Tech Intelligence Agent - Telegram Responder")

app.include_router(health_router)

logger.info("Telegram responder FastAPI app initialized")
