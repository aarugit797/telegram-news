from app.core.llm_client import call_llm
from app.prompts.processor.urgency_classifier import (
    URGENCY_SYSTEM_PROMPT, URGENCY_USER_TEMPLATE, URGENCY_JSON_SCHEMA,
)


async def classify_urgency(signal) -> str:
    """
    Classifies one signal (a cluster's representative) as BREAKING
    or STANDARD. Called once per cluster, not per raw signal -
    duplicates within a cluster don't need separate classification.
    """
    result = await call_llm(
        system_prompt=URGENCY_SYSTEM_PROMPT,
        user_message=URGENCY_USER_TEMPLATE.format(
            source=signal.source, title=signal.title, summary=signal.summary,
        ),
        trace_name="urgency-classifier",
        json_schema=URGENCY_JSON_SCHEMA,
        temperature=0.0,
    )
    return result.content["urgency"]
