"""
Turns a context-dependent question into a self-contained search query.

WHY THIS EXISTS. Conversation context reaches the tools for SYNTHESIS,
but retrieval embedded the raw question. "how do I install it?" has no
subject, so its vector means close to nothing and pgvector returns
whatever happens to sit nearest a contextless install question. The
synthesis prompts then correctly say they are unsure - which makes the
failure quiet rather than loud, and quiet failures are the ones that
survive for months.

THE REWRITE IS FOR RETRIEVAL ONLY. The answer is still written against
the question the reader actually asked. A rewrite is a search key, not a
restatement of intent.

WHY IT IS ALLOWED TO GIVE UP. A wrong rewrite is worse than no rewrite.
"how do I install it?" left alone retrieves badly and the tool says it is
unsure; rewritten to the wrong subject it retrieves confidently and the
tool answers about the wrong thing with no signal that anything went
wrong. So ambiguity returns the original unchanged, and the prompt says
that is a correct outcome rather than a failure - otherwise a model
asked to "produce a self-contained query" will always produce one, by
guessing.
"""

QUERY_REWRITER_SYSTEM_PROMPT = """You rewrite a follow-up question into a \
self-contained search query, using the conversation it belongs to.

The output is used ONLY to search a database - it is never shown to anyone and never \
answered directly. Write it as a search query, not as a reply.

RESOLVE THE REFERENCE. Replace "it", "that", "this one", "them" and similar with the \
thing they refer to, taken from the conversation.

  conversation: the assistant described the VoiceStudio repo
  question:     "how do I install it?"
  query:        "how do I install VoiceStudio"

  conversation: the assistant described DuckDB running SQL over Parquet
  question:     "is that faster?"
  query:        "is DuckDB faster than Pandas"

  conversation: the assistant described the Context Rot paper
  question:     "what did they measure?"
  query:        "what did the Context Rot paper measure"

CHANGE NOTHING ELSE. Keep the reader's own words and scope. Do not make the query \
broader, do not make it more specific, do not add qualifiers, versions, comparisons or \
technologies that the conversation does not contain. You are substituting a name for a \
pronoun, not writing a better question.

IF THE REFERENT IS AMBIGUOUS, RETURN THE QUESTION UNCHANGED and set resolved to false. \
Ambiguous means more than one thing in the conversation could plausibly be meant, or \
nothing in it fits at all. This is a CORRECT outcome, not a failure - do not pick the \
most recent subject as a tiebreak and do not guess. An unresolved question retrieves \
poorly and the answer says it is unsure, which is recoverable. A confidently wrong \
query retrieves the wrong subject and the answer sounds certain, which is not.

  conversation: the assistant described BOTH the polars repo AND the DuckDB repo
  question:     "how fast is it?"
  query:        "how fast is it?"          (unchanged - two candidates)
  resolved:     false
"""

QUERY_REWRITER_USER_TEMPLATE = """Conversation:
{context}

Question: {question}
"""

QUERY_REWRITER_JSON_SCHEMA = {
    "type": "object",
    "properties": {
        "query": {"type": "string"},
        "resolved": {"type": "boolean"},
    },
    "required": ["query", "resolved"],
}
