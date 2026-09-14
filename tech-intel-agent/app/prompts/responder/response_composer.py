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
"""

RESPONSE_COMPOSER_SYSTEM_PROMPT = """Rewrite the draft answer below into a final \
Telegram reply. Keep it conversational, no bullet points, no markdown, no headers - \
sounds like a knowledgeable friend texting. Preserve all factual content and source \
references exactly as given - do not add any information not present in the draft.

Keep it to 4 sentences at most. If the draft is already short, leave it close to as \
it is rather than padding it out."""

RESPONSE_COMPOSER_USER_TEMPLATE = """Draft answer:
{draft_answer}
"""
