"""
STUB - to be built.

WHAT: Direct LLM call with the persona prompt
(prompts/responder/smalltalk.py) - no database call at all.

WHY: Greetings/thanks/casual comments need a brief, friendly
response that nudges back to tech topics - fastest, cheapest tool
in the system.

INPUT: Message text.

OUTPUT: A short (max 2 sentence) reply string.

CONNECTS TO: Called by conversational_agent.py when intent =
SMALLTALK. Uses core/llm_client.py.
"""
