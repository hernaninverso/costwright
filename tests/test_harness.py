"""Gate F2 — fixtures sintéticas: una por categoría D4 + casos del council."""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
import ast
from extract import Extractor, extract_unit, find_cycles
from mapper import map_unit

def run_fixture(code: str, kind: str) -> dict:
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "fix.py"; f.write_text(code)
        meta = {"unit_id": "t", "file": "fix.py", "kind": kind}
        ex = extract_unit(Path(td), meta)
        return map_unit(ex, meta), ex

# ── cat 4: tipa:explicit — LangGraph con recursion_limit explícito ──
def test_tipa_explicit():
    code = '''
from langgraph.graph import StateGraph, START, END
g = StateGraph(dict)
g.add_node("a", lambda s: s)
g.add_node("b", lambda s: s)
g.add_edge(START, "a")
g.add_edge("a", "b")
g.add_edge("b", END)
app = g.compile()
app.invoke({}, config={"recursion_limit": 50})
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "tipa:explicit", r
    assert r["supersteps"] == 50
    # cadena lineal → n·max (linear probada)
    assert r["aggregation"] == "max", r

# ── cat 5: tipa:framework-default — ciclo condicional sin límite ──
def test_tipa_framework_default():
    code = '''
from langgraph.graph import StateGraph, START, END
g = StateGraph(dict)
g.add_node("agent", lambda s: s)
g.add_node("tools", lambda s: s)
g.add_edge(START, "agent")
g.add_conditional_edges("agent", lambda s: "tools", {"tools": "tools", "end": END})
g.add_edge("tools", "agent")
app = g.compile()
app.invoke({})
'''
    r, ex = run_fixture(code, "langgraph")
    assert r["category"] == "tipa:framework-default", r
    assert r["supersteps"] == 1000          # default moderno D8
    assert r["aggregation"] == "sum"        # cíclico → n·Σ (D2, fix gpt-5.5)
    assert "vacuo" in r.get("default_caveat", "")

# ── cat 3: rechaza-con-razon — while True driver ──
def test_rechaza_while_true():
    code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("a", lambda s: s)
app = g.compile()
while True:
    app.invoke({})
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "rechaza-con-razon", r
    assert r["reason"] == "while-true-driver"

# ── cat 3b: recursion_limit gigante = no acotado en la práctica ──
def test_rechaza_limit_gigante():
    code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("a", lambda s: s)
app = g.compile()
app.invoke({}, config={"recursion_limit": 1000000})
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "rechaza-con-razon", r
    assert r["reason"] == "recursion-limit-huge"

# ── cat 2: no-mapeable:send-fanout ──
def test_no_mapeable_send():
    code = '''
from langgraph.graph import StateGraph
from langgraph.types import Send
g = StateGraph(dict)
g.add_node("fan", lambda s: [Send("w", x) for x in s["items"]])
g.add_node("w", lambda s: s)
app = g.compile()
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "no-mapeable:send-fanout", r

# ── cat 2b: interrupt / human-in-the-loop ──
def test_no_mapeable_interrupt():
    code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("a", lambda s: s)
app = g.compile(interrupt_before=["a"])
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "no-mapeable:interrupt-human-in-loop", r

# ── CrewAI: max_iter explícito vs default ──
def test_crewai_explicit_y_default():
    code_exp = '''
from crewai import Crew, Agent, Task
a = Agent(role="r", goal="g", backstory="b", max_iter=5)
c = Crew(agents=[a], tasks=[])
c.kickoff()
'''
    r, _ = run_fixture(code_exp, "crewai")
    assert r["category"] == "tipa:explicit" and r["supersteps"] == 5, r
    code_def = code_exp.replace(", max_iter=5", "")
    r2, _ = run_fixture(code_def, "crewai")
    assert r2["category"] == "tipa:framework-default" and r2["supersteps"] == 20, r2

# ── CrewAI hierarchical → no-mapeable ──
def test_crewai_hierarchical():
    code = '''
from crewai import Crew, Process
c = Crew(agents=[], tasks=[], process=Process.hierarchical)
'''
    r, _ = run_fixture(code, "crewai")
    assert r["category"] == "no-mapeable:hierarchical-manager", r

# ── Agents SDK: max_turns=None DESACTIVA (D8) → rechaza ──
def test_sdk_max_turns_none():
    code = '''
from agents import Agent, Runner
a = Agent(name="x")
Runner.run_sync(a, "hola", max_turns=None)
'''
    r, _ = run_fixture(code, "agents_sdk")
    assert r["category"] == "rechaza-con-razon" and r["reason"] == "max-turns-none", r

# ── certifiability: caps detectados ──
def test_caps():
    code = '''
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph
llm = ChatOpenAI(model="gpt-4o", max_tokens=512)
g = StateGraph(dict)
g.add_node("a", lambda s: llm.invoke(s))
app = g.compile()
app.invoke({}, config={"recursion_limit": 10})
'''
    _, ex = run_fixture(code, "langgraph")
    finite = [c for c in ex["caps"] if isinstance(c.get("value"), int)]
    assert len(finite) == 1 and finite[0]["value"] == 512, ex["caps"]

# ── fixes de revisión D5 ──
def test_repl_interactivo_no_es_runaway():
    code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("a", lambda s: s)
app = g.compile()
while True:
    msg = input("You: ")
    if msg == "quit": break
    app.invoke({"m": msg})
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] != "rechaza-con-razon", r   # REPL: humano en el loop

def test_max_turns_variable_no_es_none():
    code = '''
from agents import Agent, Runner
a = Agent(name="x")
Runner.run_sync(a, "hola", max_turns=settings.max_turns)
'''
    r, _ = run_fixture(code, "agents_sdk")
    assert r["category"] == "extractor-failure" and r["reason"] == "unresolved-bound", r

def test_add_node_funcion_es_nombrado():
    code = '''
from langgraph.graph import StateGraph
def node_a(s): return s
g = StateGraph(dict)
g.add_node(node_a)
app = g.compile()
'''
    _, ex = run_fixture(code, "langgraph")
    assert ex["n_nodes_named"] == 1 and ex["n_nodes_dynamic"] == 0, ex

# ── determinismo del pipeline completo (FR-007) sobre las fixtures ──
def test_determinismo():
    code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("a", lambda s: s)
app = g.compile()
'''
    r1, _ = run_fixture(code, "langgraph")
    r2, _ = run_fixture(code, "langgraph")
    assert json.dumps(r1, sort_keys=True) == json.dumps(r2, sort_keys=True)


def test_node_interrupt_detectado():
    code = '''
from langgraph.errors import NodeInterrupt
from langgraph.graph import StateGraph
def step(s):
    if len(s["input"]) > 5: raise NodeInterrupt("too long")
    return s
g = StateGraph(dict)
g.add_node("step", step)
app = g.compile()
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "no-mapeable:interrupt-human-in-loop", r

def test_subgraph_node_detectado():
    code = '''
from langgraph.graph import StateGraph
sub = StateGraph(dict)
sub.add_node("inner", lambda s: s)
g = StateGraph(dict)
g.add_node("child", sub.compile())
app = g.compile()
'''
    r, _ = run_fixture(code, "langgraph")
    assert r["category"] == "no-mapeable:subgraph-node", r

def test_add_node_dos_args_nombre_variable_es_dinamico():
    code = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node(inner_name, inner_node)
app = g.compile()
'''
    _, ex = run_fixture(code, "langgraph")
    assert ex["n_nodes_dynamic"] == 1, ex

if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn(); print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            bad += 1; print(f"  ✗ {fn.__name__}: {str(e)[:200]}")
    print(f"{len(fns)-bad}/{len(fns)} PASS")
    sys.exit(1 if bad else 0)
