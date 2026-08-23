"""
STUB - to be built.

WHAT: Prompt for the guardrail layer - classifies an incoming user
message as PASS, INJECTION_DETECTED, or OFF_TOPIC.

WHY: This agent must ONLY discuss tech news. This prompt is what
enforces that boundary and detects attempts to override the system
prompt (prompt injection) across multiple conversation turns, not
just the current message.

CONNECTS TO: Used by responder/guardrail.py.
"""
