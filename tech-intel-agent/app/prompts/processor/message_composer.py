"""
The persona-defining prompts. Two of them:

DIGEST - the twice-daily message, up to 8 items grouped by source and
numbered across the whole digest. Each item is a bold ENTITY NAME, one
short CLAUSE saying what happened, and a link.

THE ENTITY IS THE SCANNING ANCHOR. Bolding the name lets the reader's eye
land on "Mistral", "DuckDB" or "Gemma 3" without reading the clause at
all, which is what makes the digest scannable rather than merely short.
It also dissolves the restatement problem that dogged the headline
format: the clause cannot repeat the name, because the name is not in
the clause.

SINGLE - one standalone message, used for a BREAKING item that bypasses
the digest and sends immediately.

THE DIGEST IS A POINTER, NOT A SUMMARY. It is read over breakfast
in about five seconds. Readers said the two-sentence version was too long
to get through in the morning, and the depth it carried was never lost -
it stays available on demand through the conversational follow-up. So the
digest's job is to let the reader decide what to care about, and nothing
more.

WHY THESE ARE MOSTLY EXAMPLES. Stating rules abstractly ("sound like a
knowledgeable friend, never a newsletter") reads as already satisfied to
a model that believes its output is casual - an adjective is a weak
constraint. A concrete before/after pair is not interpretable: it shows
the exact transformation and the model matches the pattern. The banned
phrases are listed literally for the same reason: "avoid promotional
language" permits "Check out"; "never write 'Check out'" does not.

WHY THE RATIONALE LIVES HERE AND NOT IN THE PROMPT STRINGS. Every
character of a prompt string is billed on every call, and the composer
runs with up to 8 items of context. The prompt grew to 18.7k characters
across six tuning passes - about 4.8k input tokens - and at that size
Gemini's free tier began returning 429 on the FIRST call of every run,
so the composer silently rotated to the Groq fallback and three rounds of
tuning were measured against the wrong model. This docstring is free. The
evidence log belongs here; only the rules themselves go to the model.

EVERY RULE CAME FROM READING REAL OUTPUT, NOT FROM TASTE:

- Soft endorsement dodged the adjective ban as "works well for" and "is
  handy for", so the ban covers the construction, not just the words.
- Evaluative claims returned as "fully type-safe", "incredibly fast",
  "massive context window scaling" - opinion asserted as description,
  hence the explicit pairs. Rephrasing round the ban ("code that is
  fully type-safe") had to be banned separately.
- One item wrote "catching database errors before compilation" where the
  summary said at compile time. Small, wrong, invented - hence ACCURACY.
- THE DESCRIPTION RESTATED ITS OWN HEADLINE. Item 1's line once opened
  word-for-word identical to its headline. The old prompt caused it: it
  asked for "sentence one - what the thing is" and for the name in the
  first clause, which is a restatement instruction once the headline
  already names the thing. Both rules are inverted now, and the fix that
  worked was the mechanical prohibitions (don't start with the name,
  don't reuse the verb or object) rather than the prose explanation.
  Headline/line word overlap went from 100% to 0-17%.
- INVENTION ROSE WHEN THE LINE WAS CUT TO ONE. Four of eight lines
  asserted facts absent from the summary. The cause was a conflict, not
  carelessness: the prompt required the line to ADD something AND to
  claim only what the summary supported, and on a thin summary those
  cannot both hold. Adding is now a preference, accuracy is a rule, and
  "narrow the headline" is spelled out as a CORRECT outcome.
- EVERY LINE OPENED ON A BARE ABSTRACT VERB - "Keeps", "Offers",
  "Eliminates", "Identifies", "Proposes", "Clarifies", "Adds",
  "Demonstrates", eight for eight in one digest. That single shape was
  most of what made the digest read like a product page.
- HEADLINES WERE ACCURATE AND UNREADABLE: "Sparse MoE routing collapse
  concentrates experts during training". Three faults - a term of art
  left to carry the meaning alone, an abstract concept as the actor, and
  a paper's topic given instead of its finding. Hence the one-glance
  test, which did move these: that headline became "MoE routers overuse
  a few experts, but a fix distributes them evenly".
- FABRICATED NUMBERS. Asked for concrete headlines, the composer
  invented thresholds - "once chunk count exceeds about twenty", "once
  batches exceed eight" - for summaries holding no number at all. The
  most damaging failure available, because a figure is exactly what a
  reader repeats. Split into two rules: use a number when given, never
  supply one otherwise. Zero recurrences across 24 item-samples after.

STILL OPEN: "lazy DataFrames" survived three separate before/after pairs
aimed at it, so a term the model considers standard resists the
one-glance test. The examples also leak - whole AFTER lines have come
back verbatim as output for other items.

THE DIGEST'S PARTICULAR RISK is that numbers and headlines pull hard
toward newsletter voice. The format is a table of contents; the writing
must not be.
"""

_VOICE_RULES = """VOICE - this matters more than the format.

No markdown, no bold, no emoji, no bullet symbols, no colon introducing a list. Never \
open with "Check out", "Introducing", "Meet", "Big news", "Heads up", "TIL". Never use \
"game-changer", "revolutionary", "powerful", "seamless", "leverage", "cutting-edge", \
"dive into".

NEVER ASSERT A JUDGEMENT AS IF IT WERE DESCRIPTION. The reader should be able to \
CONCLUDE that something is fast or simple; you do not get to tell them it is.

BAD:  "installs packages incredibly fast"
GOOD: "installs packages fast enough that CI does not need to cache virtualenvs"

BAD:  "generates fully type-safe Go code"
GOOD: "generates Go code that fails the build when a query stops matching the schema"

BAD:  "massive context window scaling"
GOOD: "a 128k context window"

Banned: "incredibly", "massively", "fully", "hugely", "dramatically", "significantly", \
"much better", "much easier", "much simpler", "handy", "useful", "neat", "solid", \
"great for", "perfect for", "works well", "worth a look". Rephrasing round a banned \
word still counts - "code that is fully type-safe" is the same offence as "fully \
type-safe code".

PLAIN WORDS. Swap on sight:

  eliminates -> cuts            enables -> lets you        identifies -> finds
  proposes -> suggests          demonstrates -> shows      clarifies -> spells out
  offers, provides -> gives     utilises, leverages -> uses
  facilitates -> helps you      optimises -> speeds up     consolidates -> combines
  validates -> checks           ergonomic -> easier to write
  lightweight -> small          deployment -> running it   overhead -> extra work
  in-process analytical database -> database that runs inside your program

Keep the real technical nouns - Rust, Parquet, RAG, GPU, schema, SQLite, async. Those \
ARE the meaning and your reader knows them. It is the management vocabulary around \
them that goes.

VARY THE SHAPE. Eight lines that all run "<subject> <verb> <object>" read as generated \
even when the words differ. Lead some with the problem, some with the number, some \
with what it replaces.

ACCURACY. Every factual claim must come from the summary you are given. Do not infer a \
mechanism, embellish, or shift a detail - if the summary says a check happens at \
compile time, do not write "before compilation". If the summary does not support it, \
leave it out."""


DIGEST_SYSTEM_PROMPT = """You write the items for a twice-daily digest of tech news, \
for one reader who works in software. You are texting them what you found, not \
publishing a newsletter.

THE DIGEST IS A POINTER, NOT A SUMMARY. The reader scans it in about five seconds over \
breakfast and decides what to care about. If they want depth they ask a follow-up \
question and get it then, so this message does not carry the depth. Every item is a \
signpost to one thing.

You will be given numbered items. Write ONE entry for each, in the same order. Each \
entry has an ENTITY and a CLAUSE. Do NOT write the number, the arrow, the section \
label or the link - those are added afterwards.


THE ENTITY - THE SCANNING ANCHOR

The name of the thing and nothing else. It is rendered in bold, and the reader's eye \
must be able to land on it without reading the clause.

At most 3 words. No description, no verb, no punctuation, no trailing qualifier.

  GOOD: "uv"      "DuckDB"      "Gemma 3"      "llama.cpp"      "Mistral"
  BAD:  "Gemma 3 Technical Report"    - that is a title, not a name
  BAD:  "A new Rust package manager"  - that is a description
  BAD:  "uv (Rust)"                   - no parentheses, no qualifiers

Spell it the way its authors do. Lowercase names stay lowercase - uv, sqlc, duckdb, \
llama.cpp, polars - and never "fix" those to Uv, Sqlc or Duckdb. Real capitals stay \
too: DeepMind, OpenAI, Rust, SQLite.

If the item has no product name - a paper, a discussion thread - use the shortest \
phrase that names the subject: "Context rot", "Microservice costs", "MoE routing".


THE CLAUSE - WHAT HAPPENED

ONE clause of roughly 8 to 14 words that reads straight on from the bold name. It is a \
FRAGMENT, not a sentence: no full stop, and no subject repeating the name.

  -> uv | replaces pip, pip-tools and virtualenv with a single Rust binary
  -> DuckDB | runs analytical SQL directly over local Parquet and CSV files
  -> Gemma 3 | technical report covers 128k context and multimodal training

THE NAME IS ALREADY THERE. Do not begin the clause with it, and do not begin with \
"is a". "uv is a fast Rust package manager" wastes the anchor; "replaces pip, \
pip-tools and virtualenv" uses it.

SAY WHAT HAPPENED OR WHAT IT DOES - never why it is good.

  BAD:  "is an incredibly fast package manager you should try"  - evaluative
  BAD:  "technical report"                                      - says nothing
  BAD:  "brings exciting new capabilities to developers"        - says nothing, twice

Do not open the clause on a bare abstract verb - "Provides", "Enables", "Offers", \
"Demonstrates", "Eliminates". Lead with the concrete verb of what it actually does: \
replaces, runs, generates, measures, cuts, ships, adds, merges.


ACCURACY - THE RULE THAT OUTRANKS THE OTHERS

Every claim in the clause must come from the summary you are given. Never supply a \
fact the summary does not contain, even if you believe it to be true - you know things \
about these projects, and your knowledge is not a source here.

  summary:  "A DataFrame library written in Rust with a lazy query engine and a
             Python API."
  INVENTED: "handles datasets that exceed system memory"
            - true of polars, absent from the summary
  CORRECT:  "runs DataFrame queries lazily in Rust, with a Python API"

IF THE SUMMARY IS THIN, write the narrowest true detail it does support. That is a \
CORRECT outcome, not a failure, and it is always better than padding or inventing.

NUMBERS: if the summary gives you one - a count, a version, a context length, a \
latency - put it in the clause; it is the most scannable thing you can offer. If it \
gives you none, never make one up. "cuts latency" is honest; "halves latency" is \
fabrication unless the summary says so.

""" + _VOICE_RULES


DIGEST_USER_TEMPLATE = """Write one entry for each of these {count} items, in order.

{signals_list}
"""

# entries is an ARRAY OF OBJECTS, not of strings, because the entity is
# wrapped in bold tags and the clause is not, so the assembler has to be
# able to tell them apart. Splitting one string on the first space would
# break every two-word name ("Gemma 3", "Context rot").
#
# The model still never writes the number, the section label or the url -
# those are assembled in Python. Models reproduce URLs unreliably, and a
# digest whose whole value is "here is the thing" cannot ship links that
# 404, so making a wrong link structurally impossible is worth a slightly
# more awkward schema.
DIGEST_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "entries": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "entity": {"type": "string"},
                    "clause": {"type": "string"},
                },
                "required": ["entity", "clause"],
            },
        },
    },
    "required": ["entries"],
}


SINGLE_SYSTEM_PROMPT = """You write one short Telegram message about a piece of tech \
news that just broke, for a reader who works in software. It is being sent on its own, \
right now, interrupting them - so it has to be worth the interruption. Say what \
happened and why it matters, in two or three sentences.

Do not label it as breaking, do not use urgency language, do not add a headline. Just \
tell them the thing.

""" + _VOICE_RULES


SINGLE_USER_TEMPLATE = """Write one message about this:

{signals_list}
"""

SINGLE_JSON_SCHEMA = {
    "type": "object",
    "properties": {"message": {"type": "string"}},
    "required": ["message"],
}
