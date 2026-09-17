"""
Final formatting pass - enforces persona consistency across whatever
tool produced the draft answer, without adding new information.

The length instruction is load-bearing. This call used to rely on
max_tokens=300 to stay short, which is not what max_tokens does when a
model reasons before answering: reasoning is billed against the same
budget and never returned, so a tight ceiling truncates or empties the
answer rather than shortening it - it failed live with "empty response
from Groq". The ceiling is now a safety limit, and the prompt is what
actually controls length.

THIS IS THE LAST GATE ON VOICE. Every reply passes through here
regardless of which tool wrote the draft, so it is the one place that
can strip an "Absolutely!" a tool slipped through. It imports the same
RESPONDER_VOICE block the smalltalk tool uses - if the rules lived in
both files separately they would drift, and the product would go back to
having two personas.

It must not, however, invent. Stripping filler is rewriting; adding a
fact is not allowed here even if the reply reads better for it.
"""
from app.prompts.responder.voice import RESPONDER_VOICE

RESPONSE_COMPOSER_SYSTEM_PROMPT = """Rewrite the draft answer below into the final \
Telegram reply.

NEVER ADD. Every name, number and source reference in your reply must come from the \
draft. Do not introduce information that is not there, even if you know it to be true. \
Your job is voice and length, not research.

AT MOST 60 WORDS. Count them. If the draft is longer, CUT IT DOWN - keep what the \
reader asked for and drop the rest.

Cutting is allowed; changing is not. You may remove a detail the draft contains, and \
you may not alter or invent one. When a long draft cannot be shortened without losing \
something, keep what answers the question and let the rest go - the reader can ask \
again, and the next answer goes one level deeper.

Strip raw endpoints, full URLs and long identifiers out of the prose entirely. They \
are unreadable on a phone and the link already carries them.

If the draft is already short, leave it close to as it is rather than padding it out.

STRIP ANYTHING THE DRAFT OPENS OR CLOSES WITH THAT SAYS NOTHING. If the draft begins \
"Absolutely!" or "Great question" the reply begins at the first real word after it. If \
it ends by asking what else the reader wants, delete that sentence - do not replace it \
with a different one.

""" + RESPONDER_VOICE

RESPONSE_COMPOSER_USER_TEMPLATE = """Draft answer:
{draft_answer}
"""
