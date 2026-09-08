from app.core.llm_client import call_llm
from app.prompts.processor.message_composer import (
    MESSAGE_COMPOSER_SYSTEM_PROMPT, MESSAGE_COMPOSER_USER_TEMPLATE, MESSAGE_COMPOSER_JSON_SCHEMA,
)


async def compose_messages(signals: list) -> list[str]:
    """
    Takes the final selected representative signals (max 3, from
    batch_assembly.py) and writes one short WhatsApp message per
    signal. temperature=0.7 here, not 0.0 - this is natural language
    generation meant to sound varied and human, the opposite of the
    consistent scoring calls elsewhere in the system.
    """
    signals_list_text = "\n\n".join(
        f"Signal {i+1}:\nSource: {s.source}\nTitle: {s.title}\nSummary: {s.summary}"
        for i, s in enumerate(signals)
    )

    result = await call_llm(
        system_prompt=MESSAGE_COMPOSER_SYSTEM_PROMPT,
        user_message=MESSAGE_COMPOSER_USER_TEMPLATE.format(signals_list=signals_list_text),
        trace_name="message-composer",
        json_schema=MESSAGE_COMPOSER_JSON_SCHEMA,
        temperature=0.7,
    )

    return result.content["messages"]
