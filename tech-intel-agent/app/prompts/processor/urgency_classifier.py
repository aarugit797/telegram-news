"""
Batching agent's urgency step - classifies a signal as BREAKING
(bypasses the 30-minute batch window, sends immediately) or STANDARD
(waits for the next scheduled batch).
"""

URGENCY_SYSTEM_PROMPT = """You classify a tech news signal's urgency.

BREAKING: a major model release, a significant security finding, a repo going viral \
within hours, or anything where a delay of even a few hours would make it feel stale.

STANDARD: genuinely interesting, but not time-critical - fine to wait for the next \
scheduled digest.
"""

URGENCY_USER_TEMPLATE = """Source: {source}
Title: {title}
Summary: {summary}
"""

URGENCY_JSON_SCHEMA = {
    "type": "object",
    "properties": {"urgency": {"type": "string", "enum": ["BREAKING", "STANDARD"]}},
    "required": ["urgency"],
}
