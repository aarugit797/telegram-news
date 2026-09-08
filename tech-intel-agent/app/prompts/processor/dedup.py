"""
Batching agent's dedup step - given several unsent signals (possibly
from different source agents), groups any that describe the same
underlying story into clusters, so a user is never notified about
the same thing twice just because two agents independently approved
it.
"""

DEDUP_SYSTEM_PROMPT = """You review a batch of approved tech news signals to find \
duplicates - signals from different sources describing the SAME underlying story, \
release, repo, or paper.

Group signals into clusters. Two signals belong in the same cluster only if they \
describe the same specific event - not just a similar general topic. A signal with no \
duplicates forms its own single-item cluster.

Respond with the id of every signal, assigned to exactly one cluster."""

DEDUP_USER_TEMPLATE = """Signals:
{signals_list}
"""

DEDUP_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {"type": "array", "items": {"type": "string"}},
        }
    },
    "required": ["clusters"],
}
