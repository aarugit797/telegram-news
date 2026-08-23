"""
STUB - to be built.

WHAT: Final LLM pass after the tool returns its result. Enforces
persona consistency (conversational, no markdown/bullets) and
source-grounding language, using prompts/responder/response_composer.py.
Runs an output check - if the draft response contains info not
present in the retrieved context, regenerates.

WHY: This is the last line of defense against hallucination and
off-persona responses before anything reaches the user.

INPUT: Raw tool output (answer + source signals) from
conversational_agent.py.

OUTPUT: Final formatted reply string, ready to send.

CONNECTS TO: Called last in webhook.py's background task chain,
after conversational_agent.py. Output passed to core/twilio_client.py
to actually send. Uses core/llm_client.py.
"""
