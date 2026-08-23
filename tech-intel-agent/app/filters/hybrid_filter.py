"""
STUB - to be built.

WHAT: The shared two-stage filter every one of the 5 agents calls.
Stage 1 = rules engine (hard numeric thresholds, no LLM cost).
Stage 2 = LLM judge (only runs on signals that passed stage 1).

WHY: All 5 agents follow the identical filtering PATTERN even
though their specific thresholds and prompts differ. Writing this
once and having each agent pass in its own rules + prompt avoids
duplicating the two-stage logic 5 times.

INPUT: Raw fetched signal data (dict), the source-specific rules
function, and the source-specific prompt module.

OUTPUT: (passed: bool, scores: dict, justification: str) - the
agent decides whether to write to DB based on this.

CONNECTS TO: Called by all 5 files in app/agents/. Calls
core/llm_client.py for the stage 2 LLM call.
"""
