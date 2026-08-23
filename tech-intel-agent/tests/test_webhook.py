"""
STUB - to be built.

WHAT: Integration tests for responder/webhook.py - verifies Twilio
signature verification correctly rejects invalid signatures,
verifies the whitelist/rate-limit/guardrail chain short-circuits
correctly, and verifies a valid message flows through to a response.

WHY: This is the only externally-facing endpoint in the entire
system - if signature verification is broken, anyone can spoof
messages to our agent impersonating any user.

CONNECTS TO: Tests app/responder/webhook.py and its full chain.
"""
