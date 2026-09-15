import re
from html import escape

from app.core.config import settings
from app.core.llm_client import call_llm
from app.core.logging_config import get_logger
from app.prompts.processor.message_composer import (
    DIGEST_SYSTEM_PROMPT, DIGEST_USER_TEMPLATE, DIGEST_JSON_SCHEMA,
    SINGLE_SYSTEM_PROMPT, SINGLE_USER_TEMPLATE, SINGLE_JSON_SCHEMA,
)

logger = get_logger(__name__)

# Telegram rejects a message over 4096 characters with a 400. A digest of
# 8 two-sentence items lands near 1200, so this is headroom rather than a
# constraint - but a composer that ignores its length instruction would
# otherwise lose the entire digest rather than most of it.
TELEGRAM_MAX_CHARS = 4096

# The digest's closing block. A FIXED TEMPLATE, never model output.
#
# The composer only ever writes an entity and a clause; this string is
# appended by _assemble and is identical in every digest. That is
# deliberate - it is the one sentence that has to be right every single
# time, it carries no per-item information, and a model that reworded it
# each run would eventually produce a call to action, an emoji, or a
# promise the responder cannot keep. There is nothing here for an LLM to
# add, and a wrong version would teach the reader the wrong way to use
# the product.
#
# WHY IT IS WORDED THIS WAY. It names the MECHANISM first, because "ask
# me about any of these" never told anyone how. Replying with a number is
# what the responder actually resolves - notification_history_tool maps
# that number back through batch.signal_ids to one signal - so the
# instruction is literally true rather than merely encouraging. The
# second line names the kinds of question worth asking, since a reader
# told "ask me anything" has no idea what is actually answerable.
#
# Two lines rather than one: on a phone a single long sentence wraps into
# a grey block that reads as boilerplate and gets skipped.
#
# Sentence case here, unlike the items. The item text follows the voice
# rules because it is written fresh each time and would otherwise drift
# into newsletter register; this line is fixed, so it can simply be
# well-written. Still no emoji, no exclamation, no urgency.
DIGEST_CLOSING = (
    "Reply with a number to go deeper on any of these.\n"
    "I can tell you what it actually does, how it compares to what you already "
    "use, or whether it is worth your time."
)


# Section order in the digest. Fixed rather than alphabetical so the
# reader learns where to look: code first, then research, then
# announcements, then discussion.
SOURCE_ORDER = ["github", "arxiv", "blogs", "rss", "hackernews"]

# The label shown to the reader. "arxiv" reads as a hostname; "papers"
# reads as a category.
SOURCE_LABELS = {
    "github": "github",
    "arxiv": "papers",
    "blogs": "blogs",
    "rss": "newsletters",
    "hackernews": "hackernews",
}


def order_for_digest(signals: list) -> list:
    """
    Groups by source in SOURCE_ORDER, highest composite_score first within
    each group.

    Returned as a flat list because the ORDER IS THE CONTRACT: the digest
    numbers items 1..N in exactly this sequence, and batch.signal_ids is
    stored in the same sequence, so the notification-history tool can map
    "number 3" back to a specific signal. Anything that reorders one side
    without the other silently breaks follow-up questions.
    """
    by_source: dict[str, list] = {}
    for s in signals:
        by_source.setdefault(s.source, []).append(s)

    ordered = []
    for source in SOURCE_ORDER:
        group = sorted(by_source.pop(source, []), key=lambda s: s.composite_score, reverse=True)
        ordered.extend(group)
    # Any source not in SOURCE_ORDER still gets delivered rather than
    # silently dropped - a new agent should not lose its signals just
    # because this list was not updated.
    for leftover in by_source.values():
        ordered.extend(sorted(leftover, key=lambda s: s.composite_score, reverse=True))
    return ordered


def _split_at_section(text: str, limit: int = TELEGRAM_MAX_CHARS) -> list[str]:
    """
    Splits an over-long digest at an item boundary rather than
    mid-sentence.

    Every item is a block separated by a blank line, so splitting there
    keeps each item whole - entity, clause and url stay together - and the
    numbering stays continuous across the parts. Falls back to a hard
    character split only if no boundary exists, which would mean a single
    item exceeded the limit on its own.
    """
    if len(text) <= limit:
        return [text]

    parts, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) > limit and current:
            parts.append(current)
            current = block
        else:
            current = candidate
    if current:
        parts.append(current)

    out = []
    for part in parts:
        while len(part) > limit:
            out.append(part[:limit])
            part = part[limit:]
        out.append(part)
    return out


# Matches one word, keeping the punctuation that lives INSIDE a project
# name - "whisper.cpp", "pip-tools", "astral-sh" are each one word here.
_WORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-_+]*")


def _lead_capital(headline: str, signal) -> str:
    """
    Capitalises the first letter of a headline WITHOUT mangling a name.

    The model reads "sentence case, no Title Case" as "lowercase
    everything" and produces "deepmind releases gemma 3", so the first
    capital has to be guaranteed in code rather than requested.

    But a blind headline[0].upper() is wrong in the other direction: it
    turns "uv", "sqlc" and "duckdb" into "Uv", "Sqlc" and "Duckdb", which
    are not those projects' names. It made three of eight headlines wrong
    in one digest.

    So the title decides, but it gets TWO DIFFERENT LEVELS OF TRUST,
    because the two directions fail differently.

    CAPITALS: always trusted. If the title spells the word with any
    uppercase letter, that spelling wins - "deepmind" becomes "DeepMind",
    "anthropic" becomes "Anthropic". A title that capitalises a word is
    evidence about a name no matter what kind of title it is.

    LOWERCASE: trusted only when the title is an IDENTIFIER rather than
    prose - a GitHub "owner/repo", or a word carrying a dot, underscore or
    digit. "astral-sh/uv" is an identifier, so "uv" stays lowercase.
    "The cost of microservices at small scale" is prose, so the lowercase
    "microservices" in it proves nothing and the headline still gets its
    capital.

    Both narrowings came from real output. Consulting the summary shipped
    "merging eleven microservices into two" with a lowercase m, because
    "merging" appeared in the summary's prose. Trusting a prose title then
    shipped "microservices cost study merges eleven services" for the same
    reason one level up. Prose is where ordinary words live; only an
    identifier is authoritative about how a name is spelled.
    """
    if not headline:
        return headline

    match = _WORD.match(headline)
    if not match:
        return headline
    word = match.group(0)

    rest = headline[match.end():]
    title = getattr(signal, "title", "") or ""
    spellings = [c for c in _WORD.findall(title) if c.lower() == word.lower()]

    for candidate in spellings:
        if any(ch.isupper() for ch in candidate):
            return candidate + rest

    identifier = "/" in title or any(ch.isdigit() or ch in "._" for ch in word)
    if spellings and identifier:
        return spellings[0] + rest

    return word[0].upper() + word[1:] + rest


def _assemble(signals: list, entries: list[dict]) -> str:
    """
    Builds the digest as one scannable pointer per item.

        1. <b>uv</b> replaces pip, pip-tools and virtualenv with one binary
        https://github.com/astral-sh/uv

    NO SECTION LABELS. Items are still GROUPED by source - order_for_digest
    keeps them together and that ordering is still the numbering contract -
    the reader simply is not shown the group's name. With a bold entity on
    every line the label was redundant furniture competing with the thing
    the eye is meant to land on.

    THE BOLD ENTITY IS THE POINT OF THE FORMAT. The reader scans down the
    bold names and stops only where one interests them, so the digest is
    read in about five seconds without any item being read in full. It
    also removes the restatement failure that the headline format kept
    producing: a clause cannot repeat the name when the name is not in it.

    NUMBERING IS A CONTRACT, not decoration. The reader asks "tell me more
    about 3" and responder/tools/notification_history_tool.py resolves
    that against batch.signal_ids stored in this exact order, so the
    numbers run straight through the digest and never restart per section.

    LINKS AND MARKUP ARE ASSEMBLED HERE, never written by the model.
    Models reproduce URLs unreliably, and a digest whose whole value is
    "here is the thing" cannot ship links that 404.

    EVERY MODEL-WRITTEN FRAGMENT IS ESCAPED before it goes near a tag.
    That escaping is the only reason this message can be sent with
    parse_mode=HTML while the responder's replies cannot.
    """
    blocks: list[str] = []

    for number, (signal, entry) in enumerate(zip(signals, entries), start=1):
        entity = " ".join(str(entry.get("entity", "")).split())
        # Same spelling authority as before: an identifier title keeps a
        # lowercase name ("uv"), a capital in the title wins ("DeepMind"),
        # and anything else gets a capital.
        entity = _lead_capital(entity, signal)

        # The clause is a fragment by design, so a trailing full stop is
        # the model drifting back toward sentences.
        clause = " ".join(str(entry.get("clause", "")).split()).rstrip(".")

        item = f"{number}. <b>{escape(entity)}</b>"
        if clause:
            item += f" {escape(clause)}"
        if signal.url:
            item += f"\n{escape(signal.url)}"

        blocks.append(item)

    if blocks:
        blocks.append(DIGEST_CLOSING)

    return "\n\n".join(blocks)


async def compose_digest(signals: list) -> list[str]:
    """
    Writes the twice-daily digest as ONE message, grouped by source,
    numbered straight through, with a real link under every item.

    The model writes ONLY the sentences; _assemble builds the rest. See
    that function for why the links are not the model's job.

    Returns a list because Telegram may need it split - normally one
    element. Signals must already be in digest order (see
    order_for_digest); this does not reorder them, because the caller has
    already recorded that order on the batch and the numbering has to
    match it.
    """
    signals_list_text = "\n".join(
        f"{i}. [{SOURCE_LABELS.get(s.source, s.source)}] {s.title} - {s.summary}"
        for i, s in enumerate(signals, start=1)
    )

    result = await call_llm(
        system_prompt=DIGEST_SYSTEM_PROMPT,
        user_message=DIGEST_USER_TEMPLATE.format(
            count=len(signals), signals_list=signals_list_text
        ),
        trace_name="digest-composer",
        json_schema=DIGEST_JSON_SCHEMA,
        # 0.9, higher than anywhere else in the system. Every other call
        # wants consistency - a scoring call at 0.9 would return different
        # numbers for the same repo. This one wants the opposite: a digest
        # whose items all open the same way reads as generated, and low
        # temperature is exactly what makes a model reach for the same
        # construction every time.
        temperature=0.9,
        max_tokens=4096,
    )

    entries = [e for e in result.content["entries"] if isinstance(e, dict)]

    # A short return would silently drop items - the signals are already
    # committed to this batch, so a missing entry means a delivered digest
    # that omits something the reader will never be shown again.
    if len(entries) != len(signals):
        logger.warning(
            "Digest composer returned the wrong number of entries",
            extra={"extra_fields": {"expected": len(signals), "got": len(entries)}},
        )
        if len(entries) < len(signals):
            # Fall back to title + summary rather than dropping the item.
            # A missing entry means a delivered digest that silently omits
            # something the reader will never be shown again.
            # A GitHub title is "owner/repo", and the repo half is the
            # name a reader would recognise.
            entries += [
                {"entity": s.title.split("/")[-1], "clause": s.summary}
                for s in signals[len(entries):]
            ]
        else:
            entries = entries[:len(signals)]

    digest = _assemble(signals, entries)
    parts = _split_at_section(digest)
    if len(parts) > 1:
        logger.info(
            "Digest split to fit Telegram's message limit",
            extra={"extra_fields": {"chars": len(digest), "parts": len(parts)}},
        )
    return parts


async def compose_breaking(signal) -> str:
    """
    One standalone message for a BREAKING signal, sent immediately rather
    than held for the next digest.
    """
    result = await call_llm(
        system_prompt=SINGLE_SYSTEM_PROMPT,
        user_message=SINGLE_USER_TEMPLATE.format(
            signals_list=f"[{SOURCE_LABELS.get(signal.source, signal.source)}] "
                         f"{signal.title} - {signal.summary}"
        ),
        trace_name="breaking-composer",
        json_schema=SINGLE_JSON_SCHEMA,
        temperature=0.9,
        max_tokens=4096,
    )
    # Escaped because batch_sender now sends every pipeline message with
    # rich=True, so an unescaped angle bracket here would 400.
    message = escape(" ".join(str(result.content["message"]).split()))
    if signal.url:
        message = f"{message}\n{escape(signal.url)}"
    return message[:TELEGRAM_MAX_CHARS]
