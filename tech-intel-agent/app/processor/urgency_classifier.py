"""
STUB - to be built.

WHAT: Step 2 of the batching agent - classifies each deduplicated
signal as BREAKING or STANDARD.

WHY: BREAKING signals bypass the 30-minute batch window and trigger
an immediate send. STANDARD signals wait for the next scheduled run.

INPUT: List of deduplicated signals from dedup.py.

OUTPUT: Same list, each tagged with urgency = "BREAKING" | "STANDARD".

CONNECTS TO: Called by processor/batching_agent.py after dedup.py.
Uses prompts/processor/urgency_classifier.py.
"""
