"""
STUB - to be built.

WHAT: Reads the current message plus the last 5 messages from
Conversation DB (multi-turn context matters for catching injection
attempts that build across turns), and classifies as PASS,
INJECTION_DETECTED, or OFF_TOPIC using prompts/responder/guardrail.py.

WHY: This agent must ONLY discuss tech news - not a general chatbot.
This is the component that enforces that boundary before any other
agent logic runs.

INPUT: Current message text, user_id (to fetch recent history).

OUTPUT: One of PASS | INJECTION_DETECTED | OFF_TOPIC. The latter two
short-circuit to a fixed response and never reach the conversational
agent.

CONNECTS TO: Called from webhook.py after rate_limit.py passes.
Uses core/llm_client.py, db/repository_conversation.py. Logs
injection attempts to Sentry via core/observability.py.
"""
