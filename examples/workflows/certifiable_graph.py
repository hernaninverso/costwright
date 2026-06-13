"""Demo fixture (NOT executed) — a LangGraph workflow with an EXPLICIT recursion_limit.

gasket analyzes this statically: a linear, acyclic StateGraph with an explicit `recursion_limit`
maps to `certifiable` (tipa:explicit) — an explicit ahead-of-time budget ceiling, not a vacuous
framework default. The fusion demo runs `gasket check` over this file to obtain the COST certificate.
"""
from langgraph.graph import StateGraph, START, END


def retrieve(state: dict) -> dict:
    return state


def answer(state: dict) -> dict:
    return state


g = StateGraph(dict)
g.add_node("retrieve", retrieve)
g.add_node("answer", answer)
g.add_edge(START, "retrieve")
g.add_edge("retrieve", "answer")
g.add_edge("answer", END)
app = g.compile()

# explicit recursion_limit at the call site => certifiable (explicit budget ceiling)
app.invoke({}, config={"recursion_limit": 50})
