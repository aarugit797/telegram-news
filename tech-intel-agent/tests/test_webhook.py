from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from app.responder.main import app

client = TestClient(app)


def test_webhook_rejects_invalid_signature():
    """
    A request without a genuinely valid X-Twilio-Signature must be
    rejected with 403 BEFORE any processing (whitelist check, LLM
    calls) ever runs - this is what stops anyone who discovers our
    webhook URL from spoofing messages as any user.
    """
    response = client.post(
        "/webhook/whatsapp",
        data={"From": "whatsapp:+15551234567", "Body": "hello"},
        headers={"X-Twilio-Signature": "invalid-signature"},
    )
    assert response.status_code == 403


def test_webhook_accepts_valid_signature_and_returns_immediately():
    """
    A genuinely valid signature should return 200 immediately without
    waiting for the full processing chain to complete - that runs as
    a background task, since Twilio times out webhook calls after
    roughly 5 seconds and our chain (multiple LLM calls) can't
    reliably fit inside that window.
    """
    with patch("app.responder.webhook._verify_signature", new_callable=AsyncMock) as mock_verify:
        mock_verify.return_value = True
        with patch("app.responder.webhook._process_message", new_callable=AsyncMock):
            response = client.post(
                "/webhook/whatsapp",
                data={"From": "whatsapp:+15551234567", "Body": "hello"},
                headers={"X-Twilio-Signature": "any-value"},
            )
    assert response.status_code == 200
