"""Tests del feature 005 — subgraph bound composition (v0.2.0).

Cubre el árbol de decisión + invariantes del council (2026-06-13):
  - aritmética compuesta (outer × (n_normal + Σ inner))
  - provenance ABSORBENTE (inner non_certifiable/runaway absorbe + tira el número)
  - default propaga a default_dependent; explicit-huge de UN grafo ⇒ runaway (producto grande NO)
  - depth-cap (5) / definition-cycle ⇒ non_certifiable (codex: NO runaway)
  - imported/unresolved ⇒ non_certifiable
  - no-fan-out: Send/dynamic-goto presentes ⇒ no se compone (non_certifiable)
  - backward-compat: archivo sin subgrafos = idéntico al path plano
  - SOUNDNESS: la cota compuesta nunca subestima un re-cálculo de referencia independiente.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from costwright import subgraph  # noqa: E402
from costwright.extract import extract_unit  # noqa: E402
from costwright.mapper import map_unit  # noqa: E402


# --- helpers: build a synthetic ex_flat (with subgraph_analysis) without parsing a file --------------
def _G(nodes, unmodeled=None):
    return {"nodes": [[n, 0] for n in nodes], "edges": [], "unmodeled": unmodeled}


def _ex(graphs, invoke_limit, subgraph_nodes, features=("subgraph-node",), ambiguous_names=()):
    # subgraph_nodes here are written as 4-tuples (outer, node, inner, line); the analyzer emits 5-tuples
    # (outer, node, inner, alias, line). Normalize with alias=None so synthetic tests stay compact.
    sn = [list(t[:3]) + [None] + list(t[3:]) for t in subgraph_nodes]
    return {
        "unit_id": "u", "kind": "langgraph", "status": "ok",
        "features": [{"feature": f, "line": 0} for f in features],
        "subgraph_analysis": {"graphs": graphs, "invoke_limit": invoke_limit,
                              "subgraph_nodes": sn, "ambiguous_names": list(ambiguous_names)},
    }


# --- composition arithmetic + categories ------------------------------------------------------------
def test_certifiable_composition_arithmetic():
    # outer(50) nodes [sub, b]; sub -> inner(25) nodes [a]. The subgraph WRAPPER counts too (audit-3 codex):
    # cost/super-step = n_total(2) + inner_bound(25×1) = 27 ; ceiling = 50 × 27 = 1350.
    ex = _ex({"outer": _G(["sub", "b"]), "inner": _G(["a"])},
             {"outer": 50, "inner": 25}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "tipa:explicit"
    assert r["bound_factor"] == 50 * (2 + 25)                   # == 1350
    assert "outer(explicit 50)" in r["composition"] and "inner(explicit 25)" in r["composition"]


def test_wrapper_execution_counted_codex_witness():
    # codex's minimal understatement witness: outer(1) [sub] → inner(1) [a]. TRUE executions = 2 (one
    # outer wrapper + one inner). The OLD n_normal formula gave 1 (a LIE). Must be ≥ 2.
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 1, "inner": 1}, [["outer", "sub", "inner", 0]])
    assert subgraph.compose(ex)["bound_factor"] == 1 * (1 + 1)  # == 2, not 1


def test_inner_default_inherits_parent_limit():
    # inner has NO explicit limit → inherits parent's config; conservative = max(parent, default 1000).
    # parent 50 < 1000 ⇒ inner uses 1000. cost = n_total(2) + inner(1000×1) = 1002 ; 50 × 1002 = 50100.
    ex = _ex({"outer": _G(["sub", "b"]), "inner": _G(["a"])},
             {"outer": 50}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 50 * (2 + 1000)                 # == 50100


def test_inner_inherits_LARGER_parent_limit():
    # audit-3 deepseek: parent 2000 > default 1000; inner without limit must inherit 2000, NOT 1000
    # (using 1000 would UNDERSTATE). inner bound = 2000 × 1 = 2000 ; outer = 2000 × (1 + 2000) = 4002000.
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 2000}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["bound_factor"] == 2000 * (1 + 2000)               # inner inherited 2000, not the 1000 default


def test_inner_huge_explicit_is_runaway():
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 50, "inner": 99999}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "rechaza-con-razon"                 # inner recursion_limit ≥ HUGE ⇒ effectively unbounded
    assert "bound_factor" not in r                              # absorbing — no number


def test_retry_policy_is_non_certifiable():
    # audit-3 BLOCKER: a RetryPolicy node re-runs within a super-step (not modeled in v1) ⇒ fail closed.
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"], unmodeled="uses a RetryPolicy")},
             {"outer": 50, "inner": 25}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "RetryPolicy" in r["reason"]


def test_large_composed_product_is_NOT_runaway():
    # both explicit and under HUGE individually; product is large but a legitimate certified ceiling.
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 9000, "inner": 9000}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "tipa:explicit"
    assert r["bound_factor"] == 9000 * (1 + 9000)              # 81,009,000 — reported, sound, not runaway


# --- absorbing provenance ---------------------------------------------------------------------------
def test_inner_unresolved_absorbs_to_non_certifiable():
    # inner var not present in graphs (imported / defined elsewhere) ⇒ non_certifiable, NO number.
    ex = _ex({"outer": _G(["sub"])}, {"outer": 50}, [["outer", "sub", "imported_inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r
    assert "imported" in r["reason"] or "resolvable" in r["reason"]


def test_inner_unresolved_recursion_limit_non_certifiable():
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 50, "inner": "unresolved"}, [["outer", "sub", "inner", 0]])
    assert subgraph.compose(ex)["category"] == "no-mapeable:subgraph-node"


# --- depth-cap / definition cycle ⇒ non_certifiable (NOT runaway, per codex) -------------------------
def test_definition_cycle_is_non_certifiable():
    # outer (unique top) → g1 → g2 → g1 : a cycle in the definition graph BELOW the outer.
    ex = _ex({"outer": _G(["s"]), "g1": _G(["s"]), "g2": _G(["s"])},
             {"outer": 50, "g1": 25, "g2": 25},
             [["outer", "s", "g1", 0], ["g1", "s", "g2", 0], ["g2", "s", "g1", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "cycle" in r["reason"]


def test_depth_cap_is_non_certifiable():
    # a chain g0→g1→...→g7 (depth > 5) ⇒ non_certifiable, never analyzer recursion.
    graphs = {f"g{i}": _G(["s"]) for i in range(8)}
    inv = {f"g{i}": 10 for i in range(8)}
    sub = [[f"g{i}", "s", f"g{i+1}", 0] for i in range(7)]
    # g0 is the outer (not anyone's inner); g7 has no subgraph (leaf)
    ex = _ex(graphs, inv, sub)
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "depth" in r["reason"]


# --- no-fan-out invariant (council P0) --------------------------------------------------------------
def test_send_present_refuses_composition():
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])}, {"outer": 50, "inner": 25},
             [["outer", "sub", "inner", 0]], features=("subgraph-node", "send-fanout"))
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "fan-out" in r["reason"]


def test_no_unique_outer_is_non_certifiable():
    # two graphs, each is the other's inner ⇒ no unique outer.
    ex = _ex({"a": _G(["s"]), "b": _G(["s"])}, {"a": 10, "b": 10},
             [["a", "s", "b", 0], ["b", "s", "a", 0]])
    assert subgraph.compose(ex)["category"] == "no-mapeable:subgraph-node"


# --- SOUNDNESS: composed bound never understates an independent reference ----------------------------
def _ref_worst(var, A, seen, depth, parent_limit=0):
    """Independent re-derivation of the conservative worst-case node-execution count, from FIRST PRINCIPLES
    (NOT mirroring the old buggy n_normal — that is exactly why the earlier property test missed codex's
    wrapper-counting bug). Model: a graph runs ≤ steps super-steps; per super-step EVERY node executes once
    (n_total wrappers) and a subgraph node ADDITIONALLY runs its whole inner; retries unmodeled ⇒ nc;
    a subgraph without its own limit inherits the parent's (max with the default)."""
    if depth > subgraph.DEPTH_CAP or var in seen or var not in A["graphs"]:
        return ("non_certifiable", None)
    seen = seen | {var}
    g = A["graphs"][var]
    if g.get("unmodeled"):
        return ("non_certifiable", None)
    rl = A["invoke_limit"].get(var)
    if rl == "unresolved":
        return ("non_certifiable", None)
    if isinstance(rl, int):
        steps, cat = rl, "certifiable"
    else:
        steps, cat = max(parent_limit, 1000), "default_dependent"
    if cat == "certifiable" and steps >= subgraph.HUGE_LIMIT:
        return ("runaway", None)
    subs = [(nn, iv) for (ov, nn, iv, _al, _l) in A["subgraph_nodes"] if ov == var]
    total_inner = 0
    for (_nn, iv) in subs:
        c, b = _ref_worst(iv, A, seen, depth + 1, parent_limit=steps)
        if c in ("non_certifiable", "runaway"):
            return (c, None)
        if c == "default_dependent":
            cat = "default_dependent"
        total_inner += b
    n_total = len(g["nodes"])                      # ALL nodes (each is a wrapper execution), incl. subgraph nodes
    return (cat, steps * max(n_total + total_inner, 1))


def test_soundness_composed_geq_reference_over_random_nests():
    import random
    rng = random.Random(0)
    for _ in range(2000):
        n = rng.randint(2, 6)
        names = [f"g{i}" for i in range(n)]
        graphs = {nm: _G([f"x{j}" for j in range(rng.randint(1, 3))],
                         unmodeled=("retry" if rng.random() < 0.1 else None))
                  for nm in names}
        inv = {}
        for nm in names:
            roll = rng.random()
            inv[nm] = (rng.choice([5, 25, 50, 9000, 99999]) if roll < 0.7 else
                       ("unresolved" if roll < 0.8 else None))   # None ⇒ default
        # build a DAG of subgraph links (i -> j, j>i) so no cycle, depth bounded
        sub = []
        for i in range(n):
            for j in range(i + 1, n):
                if rng.random() < 0.4:
                    sub.append([names[i], f"x_sub_{j}", names[j], None, 0])
                    graphs[names[i]]["nodes"].append([f"x_sub_{j}", 0])
        inner_vars = {iv for (_o, _nn, iv, _al, _l) in sub}
        outers = [v for v in graphs if v not in inner_vars]
        if len(outers) != 1:
            continue
        A = {"graphs": graphs, "invoke_limit": inv, "subgraph_nodes": sub, "ambiguous_names": []}
        got = subgraph._resolve(outers[0], A, seen=frozenset(), depth=0)
        ref_cat, ref_bound = _ref_worst(outers[0], A, seen=frozenset(), depth=0)
        assert got["category"] == ref_cat, (got, ref_cat, A)
        if ref_bound is not None:
            # NEVER understate: the shipped bound must be ≥ the independent worst-case.
            assert got["bound_factor"] >= ref_bound, (got["bound_factor"], ref_bound, A)


# --- end-to-end via extract+map (integration) -------------------------------------------------------
def _check_file(tmp_path, src):
    f = tmp_path / "g.py"
    f.write_text(src, encoding="utf-8")
    meta = {"unit_id": "u", "kind": "langgraph", "file": "g.py"}
    return map_unit(extract_unit(tmp_path, meta), meta)


def test_e2e_nested_certifiable(tmp_path):
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_node('b', lambda s: s)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub','b'); outer.add_edge('b',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 50 * (2 + 25)   # 1350 (wrapper counted)


def test_e2e_aliased_compile_composed_soundly(tmp_path):
    # audit-3 codex: a compiled subgraph passed via a VARIABLE (not inline) must be COMPOSED, not certified
    # as a normal node. compiled = inner.compile(); outer.add_node("sub", compiled). Same bound as inline.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "compiled = inner.compile()\n"
        "compiled.invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', compiled)\n"
        "outer.add_node('b', lambda s: s)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub','b'); outer.add_edge('b',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit"
    assert r["bound_factor"] == 50 * (2 + 25)        # composed, identical to the inline case (1350)
    assert r.get("composed") is True


def test_e2e_rebind_after_use_fails_closed(tmp_path):
    # REBINDING: `c = inner.compile()` is the value at the add_node, but a later `c = <fn>` adds a SECOND
    # binding site. Static analysis can't order-prove which value is live, so the count-based guard treats
    # `c` (2 bindings) as ambiguous and FAILS CLOSED. Conservative (this particular case is deterministically
    # the subgraph), but SOUND — the cardinal rule is never to emit a number we can't prove. No understatement.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "c = inner.compile()\n"
        "c.invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"
        "c = lambda s: s\n"   # SECOND binding of `c` ⇒ ambiguous ⇒ fail closed (no number)
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_codex_loop_built_graph_is_non_certifiable(tmp_path):
    # audit-3 codex round-8 WITNESS: a graph is (re)built and mutated inside loops, so its runtime node count
    # (100) is NOT the number of textual add_node SITES (1). A site-counting analyzer would compose against
    # the single recorded node (UNDERSTATEMENT). Loop-built / loop-mutated graphs must fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "for size in [1, 100]:\n"
        "    inner = StateGraph(dict)\n"
        "    for i in range(size):\n"
        "        inner.add_node(str(i), lambda s: s)\n"
        "compiled = inner.compile()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', compiled)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT a 1-node composed bound
    assert "bound_factor" not in r


def test_e2e_add_sequence_fails_closed(tmp_path):
    # add_sequence adds several nodes we don't individually count → fail closed (not a 1-node undercount).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_sequence([('a', lambda s: s), ('b', lambda s: s), ('c', lambda s: s)])\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_cursor_bound_method_alias_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-11 WITNESS: `add = inner.add_node; add("a",...); add("b",...)`.
    # The aliased calls have recv=None, so the nodes are NOT attributed to inner → site-counting sees 0
    # inner nodes → composed bound 2 while the true worst case is 3 (UNDERSTATEMENT). A graph whose
    # method/attribute is captured as a value must fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "add = inner.add_node\n"
        "add('a', lambda s: s)\n"
        "add('b', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge(START, 'b')\n"
        "inner.add_edge('a', END); inner.add_edge('b', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 1})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the understated bound 2
    assert "bound_factor" not in r


def test_e2e_cursor_container_receiver_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-14 WITNESS: the subgraph is added on a CONTAINER receiver
    # (`box[0].add_node("sub", inner.compile())`); the receiver is a Subscript, not a Name, so the per-graph
    # analyzer can't attribute the subgraph node → A["subgraph_nodes"] is empty → compose used to return None
    # and the FLAT path counted "sub" as ONE ordinary node (bound 3 vs true 12). Must fail closed, not flat.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 3})\n"
        "outer = StateGraph(dict)\n"
        "box = [outer]\n"
        "box[0].add_node('sub', inner.compile())\n"
        "outer.compile().invoke({}, config={'recursion_limit': 3})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount bound 3
    assert "bound_factor" not in r


def test_e2e_cursor_closure_in_loop_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-13 WITNESS: a closure `grow()` mutates an enclosing `inner` and is
    # called in a loop. `inner.add_node` is a method-call receiver (not escaped) and is NOT lexically in the
    # loop (it's in grow's body), so the escaped/loop guards miss it → inner undercounted. Built-in-build vs
    # mutated-in-grow = scope split ⇒ fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def build():\n"
        "    outer = StateGraph(dict)\n"
        "    inner = StateGraph(dict)\n"
        "    count = {'i': 0}\n"
        "    def grow():\n"
        "        count['i'] += 1\n"
        "        inner.add_node('n' + str(count['i']), lambda s: s)\n"
        "    for _ in range(3):\n"
        "        grow()\n"
        "    outer.add_node('sub', inner.compile())\n"
        "    outer.compile().invoke({}, config={'recursion_limit': 3})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT composed 3003
    assert "bound_factor" not in r


def test_e2e_factory_same_scope_composes(tmp_path):
    # a subgraph built AND mutated entirely inside ONE function (the common factory pattern) is still fully
    # visible → composes. Guards against the scope-split rule over-rejecting the legit case.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def build():\n"
        "    inner = StateGraph(dict)\n"
        "    inner.add_node('a', lambda s: s)\n"
        "    inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "    inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "    outer = StateGraph(dict)\n"
        "    outer.add_node('sub', inner.compile())\n"
        "    outer.add_node('b', lambda s: s)\n"
        "    outer.add_edge(START, 'sub'); outer.add_edge('sub', 'b'); outer.add_edge('b', END)\n"
        "    outer.compile().invoke({}, config={'recursion_limit': 50})\n"
        "    return outer.compile()\n"   # returns the COMPILED (immutable) graph — outer stays a method receiver
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit"
    assert r["bound_factor"] == 50 * (2 + 25)   # 1350 — same-scope factory composes


def test_e2e_cursor_container_passed_graph_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-12 WITNESS: a graph passed inside a CONTAINER (`mutate([inner])`)
    # evades the bare-Name passed_as_arg check; the helper mutates inner via `gs[0].add_node(...)`, so inner
    # is undercounted (composed 3 vs true 4). Any use of a graph that is NOT a method-call receiver →
    # escaped → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def n(state): return state\n"
        "def mutate(gs):\n"
        "    gs[0].add_node('i2', n)\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i1', n)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 1})\n"
        "mutate([inner])\n"                       # inner passed inside a list → not a method-call receiver
        "outer = StateGraph(dict)\n"
        "outer.add_node('a', n)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT composed bound 3
    assert "bound_factor" not in r


def test_e2e_graph_passed_to_helper_fails_closed(tmp_path):
    # nodes added by a helper the graph is passed INTO are attributed to the helper's param, not `inner`,
    # so inner's node count is undercounted → fail closed (codex r8 follow-on).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def add_many(g):\n"
        "    g.add_node('a', lambda s: s); g.add_node('b', lambda s: s)\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('seed', lambda s: s)\n"
        "add_many(inner)\n"                       # adds 2 nodes to inner, invisible to per-graph attribution
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_loop_added_nodes_fail_closed(tmp_path):
    # nodes added in a loop on a graph that IS used as a subgraph ⇒ count not bounded ⇒ fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "for name in ['a', 'b', 'c']:\n"
        "    inner.add_node(name, lambda s: s)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_star_import_poisons_composition(tmp_path):
    # audit-3 codex round-7 WITNESS: `from X import *` can rebind ANY name to an unseen value, so no name
    # resolves soundly. Identical to test_e2e_nested_certifiable (which composes 1350) EXCEPT for the star
    # import — which must force fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from somewhere import *\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_node('b', lambda s: s)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub','b'); outer.add_edge('b',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # composes 1350 WITHOUT the star; fail closed WITH it
    assert "bound_factor" not in r


def test_e2e_codex_param_shadow_is_non_certifiable(tmp_path):
    # audit-3 codex round-6 WITNESS: a function PARAMETER `inner` shadows a 1-node module graph of the same
    # name and is compiled inline; the function is called with a 10-node graph. A name-keyed map (scope-blind)
    # would compose the module's 1-node `inner` (UNDERSTATEMENT). Counting params as binding sites makes
    # `inner` bound 2× ⇒ ambiguous ⇒ fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('only', lambda s: s)\n"          # module `inner`: 1 node
        "big_graph = StateGraph(dict)\n"
        "for i in range(10):\n"
        "    big_graph.add_node(str(i), lambda s: s)\n"
        "outer = StateGraph(dict)\n"
        "def attach(inner):\n"                            # param `inner` shadows the module graph
        "    outer.add_node('sub', inner.compile())\n"
        "attach(big_graph)\n"                             # runtime `inner` is the 10-node big_graph
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the module 1-node compose
    assert "bound_factor" not in r


def test_e2e_factory_rebind_after_compile_fails_closed(tmp_path):
    # codex round-5 follow-on (the stale-binding hole): `c = small.compile()` then `c = make_big()` (a
    # non-compile rebind that, at runtime, may return a HUGE subgraph). compiled_from would still point at
    # `small`; counting EVERY binding site (not just compiles) makes `c` ambiguous ⇒ fail closed, NOT a
    # bound resolved against the stale small graph.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from factories import make_big\n"
        "small = StateGraph(dict)\n"
        "small.add_node('x', lambda s: s)\n"
        "c = small.compile()\n"
        "c = make_big()\n"          # non-compile rebind → runtime c may be a 100-node subgraph
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_codex_cross_scope_pollution_is_non_certifiable(tmp_path):
    # audit-3 codex round-4 WITNESS: a never-called function locally binds `c = small.compile()`, while the
    # MODULE-level `c` is an IMPORTED 100-node subgraph used as a node. A scope-blind name map would resolve
    # `c` → `small` and emit a tiny bound (UNDERSTATEMENT). The ambiguity guard (c is imported + bound in a
    # sibling scope) must FAIL CLOSED, never emit a number.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from elsewhere import c\n"               # imported compiled subgraph (opaque, possibly huge)
        "def never_called():\n"
        "    small = StateGraph(dict)\n"
        "    small.add_node('a', lambda s: s)\n"
        "    c = small.compile()\n"                # SAME name `c`, bound in a sibling (never-called) scope
        "    return c\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"              # runtime uses the IMPORTED c, not the sibling's local
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT a tiny composed bound
    assert "bound_factor" not in r


def test_e2e_codex_class_body_scope_is_non_certifiable(tmp_path):
    # audit-3 codex round-5 WITNESS: a CLASS body binds `c = small.compile()`; the class body is its OWN
    # lexical scope. A module-level `c = large.compile()` is the real runtime value of the node. If class
    # bodies collapsed to module scope, `c` would resolve to `small` (UNDERSTATEMENT). With ClassDef getting
    # its own scope id, `c` is bound in 2 scopes ⇒ ambiguous ⇒ fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "large = StateGraph(dict)\n"
        "large.add_node('a', lambda s: s); large.add_node('b', lambda s: s)\n"
        "c = large.compile()\n"
        "class Noise:\n"
        "    small = StateGraph(dict)\n"
        "    small.add_node('x', lambda s: s)\n"
        "    c = small.compile()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('actual_large', c)\n"
        "outer.add_edge(START,'actual_large'); outer.add_edge('actual_large',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the bogus small-resolved number
    assert "bound_factor" not in r


def test_e2e_codex_branch_rebinding_is_non_certifiable(tmp_path):
    # audit-3 codex round-5 WITNESS: SAME name, two classifiable bindings in the SAME scope under control
    # flow. Static last-wins would compose `small` while runtime may be `large` (UNDERSTATEMENT). Counting
    # bindings (not scopes) ⇒ `c` bound 2× ⇒ ambiguous ⇒ fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import random\n"
        "large = StateGraph(dict)\n"
        "large.add_node('a', lambda s: s); large.add_node('b', lambda s: s)\n"
        "small = StateGraph(dict)\n"
        "small.add_node('x', lambda s: s)\n"
        "if random.random() < 0.5:\n"
        "    c = large.compile()\n"
        "else:\n"
        "    c = small.compile()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the smaller branch's bound
    assert "bound_factor" not in r


def test_e2e_ambiguity_guard_is_load_bearing(tmp_path):
    # Unique outer (a and b are BOTH inners inline), yet `c` is bound 2× and used as a node. This must fail
    # via the AMBIGUITY GUARD itself (not the 'no unique outer' backstop), proving the guard catches a
    # mis-resolvable alias even when the graph topology is otherwise composable.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import random\n"
        "a = StateGraph(dict)\n"
        "a.add_node('1', lambda s: s); a.add_node('2', lambda s: s)\n"
        "b = StateGraph(dict)\n"
        "b.add_node('1', lambda s: s)\n"
        "if random.random() < 0.5:\n"
        "    c = a.compile()\n"
        "else:\n"
        "    c = b.compile()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('p', a.compile())\n"
        "outer.add_node('q', b.compile())\n"
        "outer.add_node('sub', c)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "ambiguous" in r["reason"] and "'c'" in r["reason"]   # the alias guard, not 'no unique outer'
    assert "bound_factor" not in r


def test_e2e_walrus_binding_two_scopes_is_non_certifiable(tmp_path):
    # a walrus `(c := X.compile())` binding the SAME name as a module assignment ⇒ ambiguous ⇒ fail closed
    # (the walrus is now captured, so it can't slip past the guard).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "big = StateGraph(dict)\n"
        "big.add_node('a', lambda s: s); big.add_node('b', lambda s: s)\n"
        "c = big.compile()\n"
        "small = StateGraph(dict)\n"
        "small.add_node('x', lambda s: s)\n"
        "vals = [(c := small.compile()) for _ in range(1)]\n"   # walrus rebinds `c` in a comprehension scope
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"


def test_e2e_same_name_two_scopes_is_non_certifiable(tmp_path):
    # a compiled-subgraph alias name reused in TWO scopes can't be resolved soundly by a name-keyed map ⇒
    # the ambiguity guard fails closed (conservative; the user can rename to certify).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def build_small():\n"
        "    s = StateGraph(dict)\n"
        "    s.add_node('a', lambda x: x)\n"
        "    compiled = s.compile()\n"             # `compiled` bound in build_small's scope
        "    return compiled\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda x: x)\n"
        "compiled = inner.compile()\n"             # `compiled` ALSO bound at module scope → ambiguous
        "compiled.invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', compiled)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"


def test_e2e_pregel_variable_is_non_certifiable(tmp_path):
    # a hand-built Pregel passed as a node var: its internals (incl. its own retries) are not modeled
    # ⇒ must fail closed, NOT be silently counted as one normal node (audit-3 codex #4).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.pregel import Pregel\n"
        "p = Pregel(nodes={})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', p)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"


def test_e2e_kwargs_spread_on_node_is_non_certifiable(tmp_path):
    # **opts may hide retry_policy / error_handler ⇒ opaque to static analysis ⇒ fail closed (audit-3 codex).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "opts = {'retry_policy': object()}\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s, **opts)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "kwargs" in r["reason"]


def test_e2e_set_node_defaults_retry_non_certifiable(tmp_path):
    # audit-3 codex (verified vs langgraph state.py): a GRAPH-WIDE RetryPolicy via set_node_defaults
    # re-runs nodes within a super-step → must fail closed, NOT silently certify a too-small ceiling.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import RetryPolicy\n"
        "inner = StateGraph(dict)\n"
        "inner.set_node_defaults(retry_policy=RetryPolicy())\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "RetryPolicy" in r["reason"]


def test_e2e_error_handler_non_certifiable(tmp_path):
    # audit-3 codex: add_node(error_handler=...) is an EXTRA node that runs on failure and is itself
    # retried — not counted in n_total, not modeled in v1 → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s, error_handler='h')\n"
        "inner.add_node('h', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "error_handler" in r["reason"]


def test_e2e_backward_compatible_no_subgraph(tmp_path):
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s)\n"
        "g.add_edge(START,'a'); g.add_edge('a',END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit"           # unchanged flat path
    assert "composed" not in r
