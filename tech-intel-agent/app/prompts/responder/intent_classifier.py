"""
Runs on any message that passed the guardrail - decides which of the
4 responder tools handles it.
"""

INTENT_CLASSIFIER_SYSTEM_PROMPT = """Classify the user's message into exactly one \
intent:

SMALLTALK - greetings, thanks, casual acknowledgements ("interesting", "cool", "lol").

NEWS_QUERY - a general tech question not clearly about one specific past notification \
("what's new with LLMs lately", "any good open source tools?").

NOTIFICATION_FOLLOWUP - a question specifically about something the bot already sent \
("tell me more about that repo you mentioned", "explain that paper from this \
morning").

WEB_QUESTION - asking about something very recent or current that likely isn't in the \
bot's own database yet ("what did OpenAI announce today", "is X still down").
"""

INTENT_CLASSIFIER_USER_TEMPLATE = """Message: {message}"""

INTENT_CLASSIFIER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {
            "type": "string",
            "enum": ["SMALLTALK", "NEWS_QUERY", "NOTIFICATION_FOLLOWUP", "WEB_QUESTION"],
        }
    },
    "required": ["intent"],
}
