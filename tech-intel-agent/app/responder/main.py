from fastapi import FastAPI

from app.core.observability import init_observability
from app.core.logging_config import get_logger
from app.responder.webhook import router as webhook_router
from app.responder.health import router as health_router

logger = get_logger(__name__)

init_observability(service_name="whatsapp-responder", include_fastapi=True)

app = FastAPI(title="Tech Intelligence Agent - WhatsApp Responder")

app.include_router(webhook_router)
app.include_router(health_router)

logger.info("WhatsApp responder FastAPI app initialized")
