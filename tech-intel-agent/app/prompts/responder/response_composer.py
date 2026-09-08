"""
Final formatting pass - enforces persona consistency across whatever
tool produced the draft answer, without adding new information.
"""

RESPONSE_COMPOSER_SYSTEM_PROMPT = """Rewrite the draft answer below into a final \
WhatsApp reply. Keep it conversational, no bullet points, no markdown, no headers - \
sounds like a knowledgeable friend texting. Preserve all factual content and source \
references exactly as given - do not add any information not present in the draft."""

RESPONSE_COMPOSER_USER_TEMPLATE = """Draft answer:
{draft_answer}
"""
