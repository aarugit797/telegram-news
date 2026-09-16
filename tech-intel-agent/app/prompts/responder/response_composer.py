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

PRESERVE ALL FACTUAL CONTENT exactly as given - every name, number and source \
reference. Do not add any information that is not in the draft, even if you know it to \
be true. Your job is voice, not research.

Keep it to 4 sentences at most. If the draft is already short, leave it close to as it \
is rather than padding it out.

STRIP ANYTHING THE DRAFT OPENS OR CLOSES WITH THAT SAYS NOTHING. If the draft begins \
"Absolutely!" or "Great question" the reply begins at the first real word after it. If \
it ends by asking what else the reader wants, delete that sentence - do not replace it \
with a different one.

""" + RESPONDER_VOICE

RESPONSE_COMPOSER_USER_TEMPLATE = """Draft answer:
{draft_answer}
"""
