"""
Every message the product sends without asking a model to write it.

WHY THESE LIVE NEXT TO voice.py. The rules in that file only reach text
an LLM produces. A fixed string bypasses them completely, so the voice
work had no effect at all on the replies below - and these were written
at different times by whoever was building that branch, which is why
they read like a system notification while the rest of the product reads
like a person.

THEY MATTER MORE THAN THEIR SIZE SUGGESTS. A reader who is not
whitelisted, or who has hit the daily cap, may see ONE of these and
nothing else, ever. It is not a footnote in their impression of the
product; it IS their impression.

TWO KINDS, and the difference is worth knowing:

  BLOCKING messages (whitelist, rate limit, cost limit, guardrail) are
  sent straight from message_handler and never touch an LLM. What is
  written here is exactly what the reader sees.

  TOOL FALLBACKS are returned from a tool and then pass through
  compose_final_response, so a model rewrites them before sending. They
  still belong here - a seed phrase steers what comes out the other
  side, and having them in one file is what stops them drifting apart
  the way the smalltalk and composer prompts did.

THE REGISTER. Short, flat, ordinary sentence case. No exclamation marks.
No apology for a rule working correctly - a rate limit doing its job is
not something to be sorry about, and "Sorry!" in front of it reads as
insincere because nothing went wrong. No explaining the policy back to
the reader and no lecture about what they should have asked instead.

Not chatty either. These stay brief. The goal is that they sound like
they came from the same source as everything else, not that they become
conversation.
"""

# Sent directly, with no model in between. Exactly these words reach the
# reader.
BLOCKED_NOT_WHITELISTED = "Invite-only for now."

BLOCKED_RATE_LIMITED = "That's it for today — catch you tomorrow."

# Distinct from the message cap on purpose: a reader who sent three long
# questions can hit this without sending many messages, and being told
# about a "message limit" then would just be confusing.
BLOCKED_COST_LIMITED = "That's today's budget used up — back tomorrow."

# Deliberately not an accusation and deliberately not an explanation. The
# old version described its own guardrail back at the reader, which
# lectures someone who may well have phrased something innocently, and
# tells an actual prompt-injection attempt exactly what tripped.
BLOCKED_INJECTION = "Not doing that one. Ask me about tech though."

BLOCKED_OFF_TOPIC = "I stick to tech. Ask me about anything I've sent you."


# Returned from a tool, then rewritten by the response composer.
NO_MATCHING_SIGNALS = "Nothing on that in my database yet."

NO_DIGEST_SENT_YET = "I haven't sent you anything yet to look back on."

NOTIFICATION_DETAILS_MISSING = "I can't pull up the details for that one."

NO_WEB_RESULTS = "Nothing current came back on that."

WEB_SEARCH_LIMIT_REACHED = (
    "That's my web searches for today. I can still work with what I've got."
)


# THE READER USED TO GET NOTHING AT ALL HERE. An exception mid-chain
# propagated out of process_message, the poller logged it, and no reply
# was ever sent - so a message that hit an exhausted LLM pool looked
# identical to the bot being switched off. Two real messages vanished
# that way in one session.
#
# Separate wording for the two cases because they mean different things
# to the reader: one is "wait a bit", the other is "that one is not
# coming back".
CAPACITY_EXHAUSTED = "I'm out of capacity right now. Try me again in a few minutes."

UNEXPECTED_ERROR = "Something broke on my side. Try that again."
