from app.core.llm_client import call_llm
from app.prompts.processor.dedup import DEDUP_SYSTEM_PROMPT, DEDUP_USER_TEMPLATE, DEDUP_JSON_SCHEMA

# Hard ceiling on how many signals go into ONE dedup prompt.
#
# get_unsent_signals already caps what reaches here, but this function
# must not rely on its caller for that: it takes a list and builds a
# prompt from all of it, so an unbounded caller means an unbounded
# prompt and a truncated response. Enforcing the bound where the prompt
# is actually built means the guarantee holds whoever calls it.
MAX_SIGNALS_PER_DEDUP = 40


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

    # Anything past the cap is passed through as its own cluster rather
    # than dropped: it stays eligible for the batch, it is just not
    # compared for duplicates this run. Losing some deduplication is a far
    # smaller harm than losing a signal. The overflow is chosen by the same
    # composite_score ordering the batch itself selects on, so what spills
    # over is what was least likely to be sent anyway.
    ranked = sorted(signals, key=lambda s: s.composite_score, reverse=True)
    to_compare = ranked[:MAX_SIGNALS_PER_DEDUP]
    overflow = ranked[MAX_SIGNALS_PER_DEDUP:]

    signals_list_text = "\n".join(
        f"id={s.id} | source={s.source} | title={s.title} | summary={s.summary}"
        for s in to_compare
    )

    # max_tokens is set explicitly. The default of 1024 is a response
    # cap, and this response is a nested list of UUIDs - roughly 40
    # tokens per signal id. Past about 25 signals the reply is TRUNCATED
    # mid-JSON, which surfaces as a parse failure rather than as
    # "too many signals", and the safety net below then scatters every
    # unparsed signal into its own cluster - silently undoing the
    # deduplication this function exists to perform.
    #
    # get_unsent_signals caps the input at 40, so 4096 leaves clear
    # headroom for the largest batch that can now reach here.
    result = await call_llm(
        system_prompt=DEDUP_SYSTEM_PROMPT,
        user_message=DEDUP_USER_TEMPLATE.format(signals_list=signals_list_text),
        trace_name="dedup-check",
        json_schema=DEDUP_JSON_SCHEMA,
        temperature=0.0,
        max_tokens=4096,
    )

    id_to_signal = {str(s.id): s for s in to_compare}
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
    for s in to_compare:
        if str(s.id) not in seen_ids:
            clusters.append([s])

    # Overflow rejoins here, each signal as its own cluster.
    clusters.extend([s] for s in overflow)

    return clusters
