"""
STUB - to be built.

WHAT: Classifies a message (that passed the guardrail) into one of:
SMALLTALK, NEWS_QUERY, NOTIFICATION_FOLLOWUP, WEB_QUESTION, using
prompts/responder/intent_classifier.py.

WHY: This routing decision determines which tool the conversational
agent uses.

INPUT: Message text.

OUTPUT: One of the 4 intent labels.

CONNECTS TO: Called from webhook.py after guardrail.py passes.
Uses core/llm_client.py. Feeds into conversational_agent.py.
"""
