from app.core.llm_client import call_llm
from app.prompts.responder.intent_classifier import (
    INTENT_CLASSIFIER_SYSTEM_PROMPT, INTENT_CLASSIFIER_USER_TEMPLATE, INTENT_CLASSIFIER_JSON_SCHEMA,
)


async def classify_intent(message: str) -> str:
    """
    Runs only on messages that passed the guardrail. Returns one of
    SMALLTALK | NEWS_QUERY | NOTIFICATION_FOLLOWUP | WEB_QUESTION,
    which conversational_agent.py uses to route to exactly one tool.
    """
    result = await call_llm(
        system_prompt=INTENT_CLASSIFIER_SYSTEM_PROMPT,
        user_message=INTENT_CLASSIFIER_USER_TEMPLATE.format(message=message),
        trace_name="intent-classifier",
        json_schema=INTENT_CLASSIFIER_JSON_SCHEMA,
        temperature=0.0,
    )
    return result.content["intent"]
