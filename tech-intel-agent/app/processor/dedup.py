from app.core.llm_client import call_llm
from app.prompts.processor.dedup import DEDUP_SYSTEM_PROMPT, DEDUP_USER_TEMPLATE, DEDUP_JSON_SCHEMA


async def deduplicate_signals(signals: list) -> list[list]:
    """
    Takes the unsent Signal objects popped from the queue, groups any
    describing the same underlying story into clusters. A signal with
    no duplicates becomes its own single-item cluster. Returns a list
    of clusters (each a list of Signal objects).

    With 0 or 1 signals there's nothing to compare, so we skip the
    LLM call entirely - zero cost for the common case of a quiet run.
    """
    if len(signals) <= 1:
        return [[s] for s in signals]

    signals_list_text = "\n".join(
        f"id={s.id} | source={s.source} | title={s.title} | summary={s.summary}"
        for s in signals
    )

    result = await call_llm(
        system_prompt=DEDUP_SYSTEM_PROMPT,
        user_message=DEDUP_USER_TEMPLATE.format(signals_list=signals_list_text),
        trace_name="dedup-check",
        json_schema=DEDUP_JSON_SCHEMA,
        temperature=0.0,
    )

    id_to_signal = {str(s.id): s for s in signals}
    clusters = []
    seen_ids = set()
    for cluster_ids in result.content["clusters"]:
        cluster_signals = [id_to_signal[cid] for cid in cluster_ids if cid in id_to_signal]
        if cluster_signals:
            clusters.append(cluster_signals)
            seen_ids.update(cluster_ids)

    # Safety net - any signal the LLM's clustering somehow omitted
    # still gets included as its own cluster, rather than silently
    # disappearing from the batch entirely.
    for s in signals:
        if str(s.id) not in seen_ids:
            clusters.append([s])

    return clusters
