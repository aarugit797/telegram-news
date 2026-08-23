"""
STUB - to be built.

WHAT: The FastAPI application entrypoint for Process Two (WhatsApp
Responder). Creates the app, registers the webhook route, health
route, and initializes observability at startup.

WHY: This is the single process that stays alive listening for
Twilio webhook calls - separate from pipeline_main.py (Process One)
per our two-process architecture.

INPUT: Nothing external - this IS the entrypoint, run via
`uvicorn app.responder.main:app`.

OUTPUT: A running FastAPI app instance.

CONNECTS TO: Calls core/observability.py at startup. Registers
routes from webhook.py and health.py.
"""
