"""
STUB - to be built.

WHAT: The LangGraph-based agent that receives the classified intent
and routes to exactly one of the 4 tools (smalltalk, news_db,
notification_history, web_search).

WHY: LangGraph gives us a proper state graph with native LangSmith
tracing per node, rather than hand-rolled if/else routing - matches
the "multi-agent architecture" goal and demonstrates real agent
orchestration understanding.

INPUT: Intent label, message text, conversation context (from
token_budget.py).

OUTPUT: Raw tool output (retrieved content + draft answer) - NOT
yet the final formatted response.

CONNECTS TO: Called from webhook.py after token_budget.py. Routes
to app/responder/tools/*.py. Output goes to response_composer.py.
"""
