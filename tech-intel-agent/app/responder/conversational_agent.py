from typing import TypedDict

from langgraph.graph import StateGraph, END

from app.responder.tools.smalltalk_tool import run_smalltalk_tool
from app.responder.tools.news_db_tool import run_news_db_tool
from app.responder.tools.notification_history_tool import run_notification_history_tool
from app.responder.tools.web_search_tool import run_web_search_tool


class AgentState(TypedDict):
    intent: str
    message_text: str
    user_id: str
    # The compressed conversation history for this turn. Every tool gets
    # it, because an unresolved pronoun can land in any of them - the
    # live failure was "is it useful for me?" reaching the smalltalk tool
    # with no referent, but "how do I install it?" would reach
    # notification_history just as blind.
    context: str
    tool_output: str
    source_signals: list


async def _smalltalk_node(state: AgentState) -> AgentState:
    state["tool_output"] = await run_smalltalk_tool(state["message_text"], state["context"])
    return state


async def _news_db_node(state: AgentState) -> AgentState:
    output, signals = await run_news_db_tool(state["message_text"], state["context"])
    state["tool_output"] = output
    state["source_signals"] = signals
    return state


async def _notification_history_node(state: AgentState) -> AgentState:
    output, signals = await run_notification_history_tool(
        state["message_text"], state["user_id"], state["context"]
    )
    state["tool_output"] = output
    state["source_signals"] = signals
    return state


async def _web_search_node(state: AgentState) -> AgentState:
    output, sources = await run_web_search_tool(
        state["message_text"], state["user_id"], state["context"]
    )
    state["tool_output"] = output
    state["source_signals"] = sources
    return state


def _route_by_intent(state: AgentState) -> str:
    """
    The actual routing DECISION was already made upstream by
    responder/intent_classifier.py - this just maps that label onto
    which graph node runs. We use LangGraph here rather than a plain
    if/elif specifically for its native per-node LangSmith tracing,
    and because it makes a future multi-tool turn (calling more than
    one tool for one message) a graph change rather than a rewrite -
    not because today's single-tool routing itself requires a graph.
    """
    return {
        "SMALLTALK": "smalltalk",
        "NEWS_QUERY": "news_db",
        "NOTIFICATION_FOLLOWUP": "notification_history",
        "WEB_QUESTION": "web_search",
    }.get(state["intent"], "smalltalk")


def _build_graph():
    graph = StateGraph(AgentState)
    graph.add_node("smalltalk", _smalltalk_node)
    graph.add_node("news_db", _news_db_node)
    graph.add_node("notification_history", _notification_history_node)
    graph.add_node("web_search", _web_search_node)

    graph.set_conditional_entry_point(
        _route_by_intent,
        {
            "smalltalk": "smalltalk",
            "news_db": "news_db",
            "notification_history": "notification_history",
            "web_search": "web_search",
        },
    )

    for node in ["smalltalk", "news_db", "notification_history", "web_search"]:
        graph.add_edge(node, END)

    return graph.compile()


_compiled_graph = _build_graph()


async def run_conversational_agent(
    intent: str, message_text: str, user_id: str, context: str = ""
) -> dict:
    """
    Runs the compiled graph - routes to exactly one tool node based
    on the already-classified intent, returns that tool's output plus
    which signals (if any) it was grounded in, for
    response_composer.py to build the final reply from.
    """
    initial_state: AgentState = {
        "intent": intent, "message_text": message_text, "user_id": user_id,
        "context": context, "tool_output": "", "source_signals": [],
    }
    final_state = await _compiled_graph.ainvoke(initial_state)
    return {"output": final_state["tool_output"], "source_signals": final_state["source_signals"]}
