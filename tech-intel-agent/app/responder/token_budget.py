from app.core.llm_client import call_llm
from app.core.logging_config import get_logger
from app.db.repository_conversation import (
    count_messages_between,
    get_latest_summary,
    get_messages_between,
    get_recent_messages,
    save_summary,
)
from app.db.session_conversation import get_conversation_session

logger = get_logger(__name__)

# The tail that is always sent verbatim. Pronoun resolution happens
# against these, so they must never be compressed - "it" in the current
# message usually refers to something one or two turns back.
RAW_MESSAGES_TO_KEEP = 5

# How many unsummarised messages must pile up before the summarizer runs.
#
# The old code regenerated on EVERY message past the tenth, which put an
# LLM call in front of every single reply for any conversation of real
# length. One exchange is two rows (inbound + outbound), so 10 rows is
# about five exchanges: the summary is at most five exchanges stale,
# those five are still present raw or near-raw in the tail, and the call
# fires roughly a tenth as often as it used to.
SUMMARY_REFRESH_EVERY = 10

# A rolling summary is repeatedly fed its own output, and text folded
# into itself drifts longer and vaguer with every pass. The prompt asks
# for a length, but a prompt is a request - this is the guarantee.
SUMMARY_MAX_CHARS = 1200

SUMMARIZE_SYSTEM_PROMPT = """You maintain a running summary of one conversation \
between a reader and a tech-news assistant.

You are given the summary so far and the messages that have happened since. Produce the \
NEW COMPLETE SUMMARY - one that stands alone and replaces the old one.

REWRITE, DO NOT APPEND. You are not adding a paragraph to the end. Merge the new \
messages into the existing summary as though writing it fresh with everything you now \
know, and keep the result under 150 words no matter how long the conversation gets. \
This is a rolling summary: your output becomes the input next time, so anything that \
grows each round grows without limit.

KEEP CONCRETE FACTS AND DROP CHATTER. What the reader asked about, which specific \
projects, papers and tools were discussed by name, any numbers, and anything they said \
about themselves or their work. A later message may refer back to any of it with just \
"it" or "that one", so names are the part that must survive. Greetings, thanks and \
acknowledgements carry nothing - leave them out.

Write plain prose. No bullet points, no headers."""

SUMMARIZE_USER_TEMPLATE = """Summary so far:
{previous_summary}

Messages since then:
{new_messages}
"""


def _render(messages) -> str:
    return "\n".join(f"[{m.direction}] {m.message_text}" for m in messages)


def _cap(text: str) -> str:
    """
    Enforces the length ceiling the prompt only asks for.

    Trims at the last sentence end before the cap rather than mid-word,
    so a capped summary still reads as prose - it is about to be fed back
    into the model as its own input, and a fragment ending mid-clause
    invites it to "finish" the thought on the next pass.
    """
    text = " ".join(text.split())
    if len(text) <= SUMMARY_MAX_CHARS:
        return text

    window = text[:SUMMARY_MAX_CHARS]
    cut = max(window.rfind(". "), window.rfind("! "), window.rfind("? "))
    return window[:cut + 1] if cut > 0 else window


async def get_conversation_context(user_id) -> str:
    """
    Returns one string ready to inject into that turn's prompt: a rolling
    summary of the older conversation, plus the last few raw messages.

    THE SUMMARY ACCUMULATES. Each regeneration reads the messages since
    the previous summary's covers_to and folds them into the previous
    summary text, so covers_from stays pinned to the start of the
    conversation and old material survives compression instead of
    scrolling out.

    This replaces a sliding window that only looked like a summary. It
    fetched exactly the last 10 messages and summarised items 6-10 of
    them, so anything older than the ten most recent was never
    summarised and never read again - the context was bounded because
    history was being discarded, not because it was being compressed. It
    also regenerated on every message past the tenth.

    WHAT IS STILL LOSSY, deliberately: compression is lossy. A fact from
    message 2 survives as long as the summarizer judges it worth keeping,
    and the prompt is explicit that names and numbers are what must
    survive. This is a bounded-context mechanism, not an archive - the
    messages table remains the complete log.
    """
    async with get_conversation_session() as session:
        raw_tail = await get_recent_messages(session, user_id, limit=RAW_MESSAGES_TO_KEEP)

        if not raw_tail:
            return ""

        raw_text = _render(reversed(raw_tail))

        # Everything older than the tail is summary territory. Bounding
        # the summary here is what stops it duplicating the messages the
        # caller is about to send verbatim anyway.
        oldest_raw = raw_tail[-1].timestamp

        summary = await get_latest_summary(session, user_id)
        since = summary.covers_to if summary else None

        pending = await count_messages_between(
            session, user_id, after=since, before=oldest_raw
        )

        if pending < SUMMARY_REFRESH_EVERY:
            if not summary:
                return raw_text
            return f"Earlier context: {summary.summary_text}\n\nRecent messages:\n{raw_text}"

        to_fold = await get_messages_between(
            session, user_id, after=since, before=oldest_raw
        )
        previous_text = summary.summary_text if summary else "(nothing yet)"

        result = await call_llm(
            system_prompt=SUMMARIZE_SYSTEM_PROMPT,
            user_message=SUMMARIZE_USER_TEMPLATE.format(
                previous_summary=previous_text, new_messages=_render(to_fold)
            ),
            trace_name="history-summarizer",
            temperature=0.0,
            # Reserved lane - a user is waiting on this reply.
            lane="responder",
        )
        summary_text = _cap(str(result.content))

        # covers_from stays at the ORIGINAL start once a summary exists.
        # It is the claim "this text represents the conversation from
        # here", and folding the previous summary in means that claim
        # still reaches back to the beginning. Moving it to this batch's
        # first message would say the older material had been dropped -
        # which is exactly the bug being fixed.
        await save_summary(session, {
            "user_id": user_id,
            "summary_text": summary_text,
            "covers_from": summary.covers_from if summary else to_fold[0].timestamp,
            "covers_to": to_fold[-1].timestamp,
        })

        logger.info(
            "Conversation summary regenerated",
            extra={"extra_fields": {
                "user_id": str(user_id),
                "messages_folded": len(to_fold),
                "summary_chars": len(summary_text),
                "had_previous": summary is not None,
            }},
        )

        return f"Earlier context: {summary_text}\n\nRecent messages:\n{raw_text}"


async def get_recent_turns(user_id, turns: int = 3) -> str:
    """
    The last few raw messages, for the intent classifier.

    Deliberately NOT get_conversation_context: that one may fire the
    history summarizer, and the classifier does not need a summary of
    last week to tell whether "is it useful for me?" contains a pronoun.
    It needs the turn immediately before. This is one indexed read and no
    LLM call.

    SKIPS THE NEWEST ROW. process_message saves the inbound message
    before classifying, so the most recent row IS the message being
    classified - including it would just show the model its own input
    twice and push the actual referent out of the window.
    """
    async with get_conversation_session() as session:
        recent = await get_recent_messages(session, user_id, limit=turns + 1)

    previous = recent[1:] if recent else []
    return "\n".join(f"[{m.direction}] {m.message_text}" for m in reversed(previous))
