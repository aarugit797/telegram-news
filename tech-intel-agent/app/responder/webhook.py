"""
STUB - to be built.

WHAT: POST /webhook/whatsapp route. First verifies the Twilio
signature (X-Twilio-Signature header) - rejects with 403 if invalid.
Immediately returns 200 OK to Twilio, then processes the message in
a background async task (whitelist -> rate limit -> guardrail ->
intent classifier -> token budget -> conversational agent ->
response composer -> reply sent).

WHY: This is the single entry point for every user message. The
200-then-process-async pattern is required because Twilio times out
webhook calls after 5 seconds - we can't do all our LLM processing
inline before responding.

INPUT: Twilio's webhook POST payload (from number, message body,
message SID, signature header).

OUTPUT: Immediate 200 OK to Twilio. The actual WhatsApp reply is
sent later via a separate Twilio API call from the background task.

CONNECTS TO: Registered in main.py. Chains through whitelist.py,
rate_limit.py, guardrail.py, intent_classifier.py, token_budget.py,
conversational_agent.py, response_composer.py, then
core/twilio_client.py to send the reply.
"""
