"""
STUB - to be built.

WHAT: Prompt for the batching agent's deduplication step - given a
list of unsent signals, identifies which ones are about the same
underlying story even if from different sources/URLs.

WHY: Two agents can independently approve the same story (e.g.
HN + arXiv both approve the same paper). This prompt catches that
via semantic comparison, not just URL matching.

CONNECTS TO: Used by processor/dedup.py.
"""
