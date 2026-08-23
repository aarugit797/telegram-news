"""
STUB - to be built.

WHAT: Step 1 of the batching agent - takes the list of unsent
signals and clusters ones that are about the same underlying story
(even from different sources/URLs) using semantic comparison via
core/llm_client.py plus straightforward URL matching.

WHY: Independent agents can both approve the same story - this
step ensures a user only gets notified about it once.

INPUT: List of unsent Signal objects from News DB.

OUTPUT: List of deduplicated signal clusters (one representative
signal per cluster), plus the IDs of merged duplicates.

CONNECTS TO: Called by processor/batching_agent.py first, before
urgency_classifier.py. Uses prompts/processor/dedup.py.
"""
