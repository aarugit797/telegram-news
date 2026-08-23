"""
STUB - to be built.

WHAT: Step 4 of the batching agent - takes the final assembled
batch and writes 2-3 separate natural-sounding WhatsApp messages
using prompts/processor/message_composer.py.

WHY: This is the persona layer - turns structured signal data into
something that reads like a friend texting, not a newsletter dump.

INPUT: A Batch object with its signals' summaries/content.

OUTPUT: A list of message strings (2-3 short messages).

CONNECTS TO: Called by processor/batching_agent.py last. Output goes
to app/sender/delivery_queue.py.
"""
