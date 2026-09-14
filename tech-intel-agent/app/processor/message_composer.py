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
        # 0.9, higher than anywhere else in the system. Every other call
        # wants consistency - a scoring call at 0.9 would return different
        # numbers for the same repo. This one wants the opposite: three
        # messages in a batch that all open the same way read as
        # generated, and low temperature is what makes a model reach for
        # the same construction every time.
        temperature=0.9,
        # Explicit, for the same reason dedup sets it: the default 1024
        # is a RESPONSE cap and this is the longest structured response
        # in the system - three WhatsApp messages written from three
        # verbose filter justifications, inside a JSON envelope. On
        # gemini-3.5-flash, reasoning tokens also count against this
        # budget, so the visible text gets cut well before 1024 tokens
        # of prose.
        #
        # Truncation here does not look like "too long". It surfaces as
        # JSONDecodeError ("Unterminated string"), burns the one format
        # retry, then fails the whole batching run - which is exactly
        # how this was found, with the composer cut off 14 characters
        # into its own output.
        max_tokens=4096,
    )

    return result.content["messages"]
