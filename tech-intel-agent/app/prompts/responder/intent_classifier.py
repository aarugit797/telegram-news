"""
Runs on any message that passed the guardrail - decides which of the
4 responder tools handles it.

IT NOW SEES THE PREVIOUS FEW MESSAGES, not just the current one.
Classifying a follow-up without history is close to impossible: "is it
useful for me?" is six words that could belong to any intent, and in
isolation the safest-looking guess is SMALLTALK. That is exactly what
happened live - the reader had just been told about a repo, asked "Ohh
wow is it useful for me ?", and got routed to the smalltalk tool, which
answered about the bot's own features because it had no referent either.

The fix is cheap: the recent turns are already in the database and
reading them costs no LLM call.
"""

INTENT_CLASSIFIER_SYSTEM_PROMPT = """Classify the user's latest message into exactly \
one intent. You are given the recent conversation so you can resolve references.

SMALLTALK - greetings, thanks, casual acknowledgements ("hey", "cool", "lol", \
"thanks"). Nothing is being asked.

NEWS_QUERY - a general tech question not clearly about one specific past notification \
("what's new with LLMs lately", "any good open source tools?").

NOTIFICATION_FOLLOWUP - a question about something the bot already sent, INCLUDING a \
bare item number ("3", "tell me about the second one", "explain that paper from this \
morning").

WEB_QUESTION - something very recent or current that likely is not in the bot's own \
database yet ("what did OpenAI announce today", "is X still down").


AN UNRESOLVED PRONOUN MEANS A FOLLOW-UP, NOT SMALLTALK.

If the message contains "it", "that", "this", "this one", "them" or "those" referring \
to something discussed earlier, it is a FOLLOW-UP about that thing. Decide which \
follow-up intent by what the referent IS: something the bot sent in a digest is \
NOTIFICATION_FOLLOWUP; anything else it asked about is NEWS_QUERY.

A reaction word attached to a real question does not make it smalltalk. "Ohh wow is it \
useful for me?" is a question about a specific thing, wrapped in a reaction.


WORKED EXAMPLES

Recent: [outbound] The VoiceStudio repo clones voices and runs locally.
Message: "Ohh wow is it useful for me ?"
-> NOTIFICATION_FOLLOWUP   ("it" = VoiceStudio, which came from a digest)

Recent: [outbound] The VoiceStudio repo clones voices and runs locally.
Message: "how would I install it?"
-> NOTIFICATION_FOLLOWUP   (still the same subject, two turns later)

Recent: [inbound] 1 can u explain what exactly does the repo
Message: "1"
-> NOTIFICATION_FOLLOWUP   (a bare number refers to a digest item)

Recent: [outbound] Here is your digest for this morning.
Message: "thanks, this is great"
-> SMALLTALK   ("this" refers to the service, and nothing is being asked)

Recent: (nothing)
Message: "hey"
-> SMALLTALK

Recent: [outbound] The Fuse paper builds verifiable ground truth for social reasoning.
Message: "what else is happening with agents this week"
-> NEWS_QUERY   (a pronoun-free general question, not about the Fuse paper)
"""

INTENT_CLASSIFIER_USER_TEMPLATE = """Recent conversation:
{context}

Latest message: {message}
"""

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
