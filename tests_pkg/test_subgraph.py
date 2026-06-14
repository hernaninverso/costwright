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
    # outer(50) nodes [sub, b]; sub -> inner [a]. The subgraph inherits the PARENT run's limit, NOT its own
    # standalone invoke (Cursor r30): inner uses max(50, default 1000) = 1000. cost/super-step =
    # n_total(2) + inner_bound(1000×1) = 1002 ; ceiling = 50 × 1002 = 50100. default_dependent (inner relies
    # on the inherited/default limit) ⇒ tipa:framework-default.
    ex = _ex({"outer": _G(["sub", "b"]), "inner": _G(["a"])},
             {"outer": 50, "inner": 25}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 50 * (2 + 1000)                 # == 50100 (inner's standalone 25 is ignored)
    assert "outer(explicit 50)" in r["composition"] and "inner(inherits 1000)" in r["composition"]


def test_wrapper_execution_counted_codex_witness():
    # codex's wrapper-counting witness: outer(1) [sub] → inner [a]. n_total counts the subgraph WRAPPER
    # (1) PLUS the inner bound. Inner inherits max(1, 1000) = 1000 (Cursor r30). bound = 1 × (1 + 1000×1).
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 1, "inner": 1}, [["outer", "sub", "inner", 0]])
    assert subgraph.compose(ex)["bound_factor"] == 1 * (1 + 1000)  # wrapper(1) + inner(1000), not n_normal(0)


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


def test_top_huge_explicit_is_runaway():
    # the TOP graph's own explicit recursion_limit ≥ HUGE ⇒ effectively unbounded ⇒ runaway. (A subgraph's
    # standalone limit is ignored under the inherited-limit model, so runaway comes from the top — Cursor r30.)
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 99999, "inner": 25}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "rechaza-con-razon"
    assert "bound_factor" not in r                              # absorbing — no number


def test_retry_policy_is_non_certifiable():
    # audit-3 BLOCKER: a RetryPolicy node re-runs within a super-step (not modeled in v1) ⇒ fail closed.
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"], unmodeled="uses a RetryPolicy")},
             {"outer": 50, "inner": 25}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "RetryPolicy" in r["reason"]


def test_large_composed_product_is_NOT_runaway():
    # top explicit 9000 (< HUGE); inner inherits max(9000, 1000) = 9000. product is large but a legitimate
    # ceiling, not runaway. inner relies on the inherited limit ⇒ tipa:framework-default.
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": 9000, "inner": 9000}, [["outer", "sub", "inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 9000 * (1 + 9000)              # 81,009,000 — reported, sound, not runaway


# --- absorbing provenance ---------------------------------------------------------------------------
def test_inner_unresolved_absorbs_to_non_certifiable():
    # inner var not present in graphs (imported / defined elsewhere) ⇒ non_certifiable, NO number.
    ex = _ex({"outer": _G(["sub"])}, {"outer": 50}, [["outer", "sub", "imported_inner", 0]])
    r = subgraph.compose(ex)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r
    assert "imported" in r["reason"] or "resolvable" in r["reason"]


def test_top_unresolved_recursion_limit_non_certifiable():
    # the TOP graph's own recursion_limit is a non-constant expression ⇒ fail closed. (A subgraph's
    # standalone unresolved limit is irrelevant — it inherits the parent — so it's the TOP that matters.)
    ex = _ex({"outer": _G(["sub"]), "inner": _G(["a"])},
             {"outer": "unresolved", "inner": 25}, [["outer", "sub", "inner", 0]])
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
    """Independent re-derivation of the conservative worst-case node-execution count, from FIRST PRINCIPLES.
    Model (Cursor r30): a graph runs ≤ steps super-steps; per super-step EVERY node executes once (n_total
    wrappers) and a subgraph node ADDITIONALLY runs its whole inner. The TOP graph uses its OWN invoke limit;
    a SUBGRAPH inherits the parent run's limit = max(parent, default), IGNORING its standalone invoke (a
    separate run). Retries/unmodeled ⇒ nc; a top with unresolved/non-constant limit ⇒ nc."""
    if depth > subgraph.DEPTH_CAP or var in seen or var not in A["graphs"]:
        return ("non_certifiable", None)
    seen = seen | {var}
    g = A["graphs"][var]
    if g.get("unmodeled"):
        return ("non_certifiable", None)
    if parent_limit > 0:
        steps, cat = max(parent_limit, 1000), "default_dependent"   # subgraph inherits; standalone ignored
    else:
        rl = A["invoke_limit"].get(var)
        if rl == "unresolved":
            return ("non_certifiable", None)
        if isinstance(rl, int):
            steps, cat = rl, "certifiable"
        else:
            steps, cat = 1000, "default_dependent"                  # top with no explicit limit ⇒ default
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
    # inner inherits the parent run's limit, NOT its standalone 25 (Cursor r30) → 50 × (2 + 1000) = 50100.
    assert r["category"] == "tipa:framework-default" and r["bound_factor"] == 50 * (2 + 1000)


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
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 50 * (2 + 1000)       # inner inherits 1000 (Cursor r30), like the inline case
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


def test_e2e_cursor_tuple_unpack_compile_alias_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-19 WITNESS: `(app,) = (inner.compile(),)` aliases the compiled
    # graph via a tuple-unpack the linker doesn't track → `app.invoke(config={recursion_limit:5000})` is not
    # attributed to inner → inner defaulted to 1000 → composed 1001 (understated, true 5001). A compile()
    # result captured in any untracked way → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "(app,) = (inner.compile(),)\n"
        "app.invoke({}, config={'recursion_limit': 5000})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the defaulted undercount 1001
    assert "bound_factor" not in r


def test_e2e_cursor_invoke_before_compile_assign_is_read(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-18 WITNESS: an `app.invoke(config={recursion_limit:5000})` inside a
    # function DEFINED before `app = inner.compile()`. Document-order linking missed inner's limit → defaulted
    # to 1000 → composed 1001 (understated). A pre-pass populates compiled_from order-independently → 5001.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "def prime():\n"
        "    app.invoke({}, config={'recursion_limit': 5000})\n"
        "app = inner.compile()\n"
        "prime()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    # the pre-pass links the invoke regardless of document order (no crash, composes). Under the r30 model
    # inner's standalone 5000 is IGNORED (a separate run) → inner inherits max(outer 1, default 1000) = 1000
    # → 1 × (n_total 1 + inner 1000) = 1001.
    assert r["bound_factor"] == 1 * (1 + 1000)
    assert r.get("composed") is True


def test_e2e_cursor_computed_config_key_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-21 WITNESS: a COMPUTED config key `{'recursion_' + 'limit': 5000}`
    # is not a constant, so it wasn't recognized as recursion_limit → defaulted to 1000 → composed 26000
    # (understated, true 130000; the key evaluates to "recursion_limit" at runtime). A config dict with any
    # non-constant key → unresolved → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_' + 'limit': 5000})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the defaulted undercount 26000
    assert "bound_factor" not in r


def test_e2e_cursor_wrapped_compile_arg_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-29 WITNESS: the add_node action arg WRAPS a compile in a function
    # call — `outer.add_node('sub', identity(inner.compile()))`. Not a direct inline compile nor a Name, so
    # it wasn't flagged → flat undercount (25 vs 26). Now an arg that CONTAINS a `.compile()` anywhere flags
    # subgraph-node → compose → fail closed (the wrapped value isn't a resolvable clean alias).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def identity(x): return x\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', identity(inner.compile()))\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 25
    assert "bound_factor" not in r


def test_e2e_cursor_with_as_compile_alias_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-28 WITNESS: a `with ... as c:` (and `for c in [...]`) bound compiled
    # alias reached add_node without being flagged → flat undercount (25 vs 26). Now ANY name bound to a
    # compile-containing expression flags subgraph-node → compose; an opaque binding like this (not a clean
    # `c = g.compile()`) fails closed (can't resolve which graph), never the flat undercount.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import contextlib\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "with contextlib.nullcontext(inner.compile()) as c:\n"
        "    outer.add_node('sub', c)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 25
    assert "bound_factor" not in r


def test_e2e_cursor_for_target_compile_alias_is_non_certifiable(tmp_path):
    # same class (Cursor r28): `for c in [inner.compile()]: outer.add_node('sub', c)` → opaque binding → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "for c in [inner.compile()]:\n"
        "    outer.add_node('sub', c)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_cursor_annotated_compile_alias_composes(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-27 WITNESS: `c: object = inner.compile()` (annotated assignment)
    # wasn't recognized as a subgraph-node → fell to the flat path (bound 25, undercount of 26). AnnAssign
    # bindings are now flagged → route to compose → 26.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "c: object = inner.compile()\n"
        "c.invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["bound_factor"] == 1 * (1 + 1000)   # inner inherits 1000 (Cursor r30); routed to compose, not the flat undercount 25
    assert r.get("composed") is True


def test_e2e_cursor_walrus_compile_alias_composes(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-26 WITNESS: `(c := inner.compile()); outer.add_node('sub', c)`. The
    # walrus-bound alias `c` was not recognized as a subgraph-node by the flat extractor → fell to the flat
    # path (bound 25, undercount of the composed 26). The walrus is now flagged → routes to compose → 26.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "(c := inner.compile())\n"
        "c.invoke({}, config={'recursion_limit': 25})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', c)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["bound_factor"] == 1 * (1 + 1000)   # inner inherits 1000 (Cursor r30); routed to compose, not the flat undercount 25
    assert r.get("composed") is True


def test_e2e_cursor_alias_of_alias_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-31 WITNESS: `compiled = inner.compile(); alias = compiled;
    # outer.add_node('sub', alias)`. `alias`'s source is a bare Name (no `.compile()`), so it wasn't flagged
    # → flat undercount (10). Name-alias chains now propagate the compiled flag → routes to compose → fail
    # closed (the alias chain isn't a clean resolvable `c = g.compile()`).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "compiled = inner.compile()\n"
        "alias = compiled\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', alias)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 10})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 10
    assert "bound_factor" not in r


def test_e2e_cursor_aliased_interrupt_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-40: `I = interrupt; I('need human')` bypassed the
    # interrupt-human-in-loop blocking. interrupt/NodeInterrupt aliases now propagate (import + assignment)
    # → aliased interrupt detected → blocks → no number.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import interrupt\n"
        "I = interrupt\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "def gate(state):\n"
        "    return I('need human')\n"
        "outer.add_node('gate', gate)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:interrupt-human-in-loop"
    assert "bound_factor" not in r


def test_e2e_cursor_tuple_unpack_aliased_send_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-39: `S, = (Send,)` (tuple-unpack alias) bypassed the fan-out
    # blocking. Send/Command aliases now propagate through ANY binding shape (`_loads_any` fixpoint) → S(...)
    # detected as Send → send-fanout blocks → no number.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import Send\n"
        "S, = (Send,)\n"
        "def route(_s):\n"
        "    return [S('sub', {}), S('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:send-fanout"
    assert "bound_factor" not in r


def test_e2e_cursor_assignment_aliased_send_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-38: `S = Send` (assignment aliasing after a plain import) bypassed
    # the fan-out blocking. Send/Command aliases now propagate through assignment chains (fixpoint) → S(...)
    # detected as Send → send-fanout blocks → no number.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import Send\n"
        "S = Send\n"
        "def route(_s):\n"
        "    return [S('sub', {}), S('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:send-fanout"
    assert "bound_factor" not in r


def test_e2e_reflective_direct_call_send_blocks(tmp_path):
    # audit-3 codex/Cursor r47: a construct invoked DIRECTLY through a literal reflective access —
    # `getattr(lgtypes,"Send")("sub", {})` — binds NO name, so alias propagation never sees it. The callee
    # expression itself resolves to "Send" now → send-fanout blocks. (Distinct from the bound `S = getattr...`.)
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "def route(_s):\n"
        "    return [getattr(lgtypes, 'Send')('sub', {}), getattr(lgtypes, 'Send')('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:send-fanout", r
    assert "bound_factor" not in r


def test_e2e_reflective_direct_call_command_and_interrupt_block(tmp_path):
    # `getattr(lgtypes,"Command")(goto=<dynamic>)` direct = dynamic goto; `getattr(lgtypes,"interrupt")()`
    # direct = human-in-loop. Both resolve via the callee and block (no binding involved).
    cmd = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "def r(_s):\n"
        "    return getattr(lgtypes, 'Command')(goto=_s['n'])\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', r)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    rc = _check_file(tmp_path, cmd)
    assert rc["category"] == "no-mapeable:dynamic-goto", rc
    assert "bound_factor" not in rc


def test_e2e_getattr_in_node_body_still_composes(tmp_path):
    # COVERAGE guard: the precise callee-resolution must NOT block on a non-construct reflective call. A node
    # body doing `getattr(llm, "invoke")(s)` ("invoke" is not a construct name) must STILL compose a bound.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "class LLM:\n"
        "    def invoke(self, x):\n"
        "        return x\n"
        "llm = LLM()\n"
        "def work(s):\n"
        "    return getattr(llm, 'invoke')(s)\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', work)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_node('b', lambda s: s)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', 'b'); outer.add_edge('b', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    # getattr with a non-construct literal does NOT set reflective_ns and does NOT resolve to a construct →
    # composition proceeds. 50 × (2 + 1000) = 50100.
    assert r["category"] == "tipa:framework-default" and r["bound_factor"] == 50 * (2 + 1000), r


def test_e2e_cursor_globals_subscript_alias_send_fails_closed(tmp_path):
    # audit-3 Cursor r46 WITNESS: `S = Send; ... globals()["S"]("x", {})` — the Send is reached via a namespace
    # subscript whose KEY is the alias NAME "S" (not the canonical "Send"), and called DIRECTLY (no binding),
    # so neither name nor literal-construct resolution catches it → composed 6003 despite a reachable fan-out.
    # Any reflective namespace access (globals/locals/vars/__dict__/dynamic getattr) now fails the composition.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import Send\n"
        "S = Send\n"
        "def route(state):\n"
        "    return [globals()['S']('x', {}), globals()['S']('x', {})]\n"
        "def x(s):\n"
        "    return s\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('route', route); inner.add_node('x', x)\n"
        "inner.add_edge(START, 'route')\n"
        "inner.add_conditional_edges('route', lambda s: 'x', {'x': 'x'})\n"
        "inner.add_edge('x', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 3})\n"
    )
    r = _check_file(tmp_path, src)
    # fail closed either way; the callee `globals()["S"]` resolves to alias "S" ⇒ the precise send-fanout
    # category (detected before compose); the reflective_ns guard is a second line of defence.
    assert r["category"] in ("no-mapeable:send-fanout", "no-mapeable:subgraph-node"), r
    assert "bound_factor" not in r


def test_e2e_dynamic_getattr_fails_closed(tmp_path):
    # a NON-literal getattr — `getattr(lgtypes, which)` — can't be statically resolved to a construct name,
    # so it must fail the composition (it could be hiding a Send). Distinct from literal getattr(_, "Send")
    # which is precisely resolved and reports send-fanout.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "which = 'Send'\n"
        "S = getattr(lgtypes, which)\n"
        "def route(_s):\n"
        "    return [S('sub', {}), S('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node", r
    assert "bound_factor" not in r


def test_e2e_cursor_module_attr_aliased_send_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-41: `import langgraph.types as lgtypes; S = lgtypes.Send` aliases Send
    # via a MODULE ATTRIBUTE (the binding value is `lgtypes.Send`, an ast.Attribute, not a bare Name), so the
    # Name-only alias propagation missed it → S(...) fan-out bypassed → composed a number. Now an alias whose
    # value references `.Send`/`.Command`/`.interrupt`/`.NodeInterrupt` by attribute is detected → fan-out blocks.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "S = lgtypes.Send\n"
        "def route(_s):\n"
        "    return [S('sub', {}), S('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:send-fanout"   # module-attr aliased Send detected → fan-out blocks
    assert "bound_factor" not in r


def test_e2e_codex_one_arg_add_node_subgraph_is_composed(tmp_path):
    # audit-3 codex CLI r45 WITNESS: LangGraph's 1-arg `outer.add_node(inner.compile())` puts the compiled
    # subgraph in arg0 (the node name is inferred from the runnable). The subgraph scan started at args[1:],
    # so arg0 was SKIPPED → no subgraph-node feature → the flat path counted ONE node → bound understated
    # (10 instead of composing the inner's 20-node chain). Now ALL positional args are scanned → composed.
    src = (
        "from langgraph.graph import StateGraph\n"
        "inner = StateGraph(dict)\n"
        + "".join(f"inner.add_node('n{i}', lambda s: s)\n" for i in range(20))
        + "outer = StateGraph(dict)\n"
        "outer.add_node(inner.compile())\n"           # 1-arg: subgraph in arg0
        "outer.compile().invoke({}, config={'recursion_limit': 10})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default"
    # 10 × (1 wrapper + 1000×20 inner) = 200010 — the inner chain is composed, NOT undercounted to 10.
    assert r["bound_factor"] == 10 * (1 + 1000 * 20), r
    assert r.get("composed") is True


def test_e2e_codex_one_arg_add_node_aliased_subgraph_is_composed(tmp_path):
    # the aliased variant of the 1-arg form: `compiled = inner.compile(); outer.add_node(compiled)`.
    src = (
        "from langgraph.graph import StateGraph\n"
        "inner = StateGraph(dict)\n"
        + "".join(f"inner.add_node('n{i}', lambda s: s)\n" for i in range(20))
        + "compiled = inner.compile()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node(compiled)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 10})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 10 * (1 + 1000 * 20), r
    assert r.get("composed") is True


def test_e2e_two_arg_add_node_not_double_counted(tmp_path):
    # regression guard for the args[1:]→args change: the normal 2-arg `add_node("sub", inner.compile())`
    # must still record the subgraph exactly ONCE (arg0 is the string name, never matches compile/alias).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START,'a'); inner.add_edge('a',END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_node('b', lambda s: s)\n"
        "outer.add_edge(START,'sub'); outer.add_edge('sub','b'); outer.add_edge('b',END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["bound_factor"] == 50 * (2 + 1000), r   # 2 outer nodes + ONE inner bound (not doubled)


def test_e2e_reflective_getattr_send_is_non_certifiable(tmp_path):
    # audit-3 codex CLI r43 + Cursor r42 (BOTH found this): `S = getattr(lgtypes, "Send")` resolves Send via a
    # CONSTANT-LITERAL reflective access — statically visible in the AST, so missing it understated a reachable
    # fan-out (composed a finite number). Constant-literal getattr/vars/__dict__/globals reflection is now
    # resolved against the construct names → fan-out blocks → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "S = getattr(lgtypes, 'Send')\n"
        "def route(_s):\n"
        "    return [S('sub', {}), S('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:send-fanout"
    assert "bound_factor" not in r


def test_e2e_reflective_getattr_command_dynamic_goto_blocks(tmp_path):
    # codex r43 flagged reflective Command too. `X = getattr(lgtypes,'Command')` then `X(goto=<dynamic>)` is a
    # dynamic goto (unbounded routing). The reflective alias must propagate so the dynamic-goto blocks compose.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "X = getattr(lgtypes, 'Command')\n"
        "def r(_s):\n"
        "    return X(goto=_s['next'])\n"   # dynamic (non-literal) goto ⇒ blocking
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', r)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    res = _check_file(tmp_path, src)
    assert res["category"] == "no-mapeable:dynamic-goto", res
    assert "bound_factor" not in res


def test_e2e_reflective_getattr_interrupt_blocks(tmp_path):
    # `X = getattr(lgtypes,'interrupt')` then `X()` = human-in-loop interrupt ⇒ blocks composition.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "import langgraph.types as lgtypes\n"
        "X = getattr(lgtypes, 'interrupt')\n"
        "def r(_s):\n"
        "    X('approve?')\n"
        "    return 'sub'\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', r)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    res = _check_file(tmp_path, src)
    assert res["category"] == "no-mapeable:interrupt-human-in-loop", res
    assert "bound_factor" not in res


def test_e2e_reflective_vars_and_dunder_dict_send_block(tmp_path):
    # the other constant-literal reflective shapes: vars(mod)["Send"] and mod.__dict__["Send"].
    for access in ("vars(lgtypes)['Send']", "lgtypes.__dict__['Send']", "globals()['Send']"):
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "import langgraph.types as lgtypes\n"
            "from langgraph.types import Send\n"   # so globals()['Send'] is defined
            f"S = {access}\n"
            "def route(_s):\n"
            "    return [S('sub', {}), S('sub', {})]\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('i', lambda s: s)\n"
            "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
            "outer = StateGraph(dict)\n"
            "outer.add_node('r', lambda s: s)\n"
            "outer.add_node('sub', inner.compile())\n"
            "outer.add_conditional_edges('r', route)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:send-fanout", (access, r)
        assert "bound_factor" not in r, (access, r)


def test_e2e_cursor_aliased_send_fanout_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-37: `from langgraph.types import Send as S` then `S(...)` bypassed
    # the send-fanout blocking (only literal `Send(...)` was detected) → composed a number despite fan-out.
    # Import aliases for Send are now tracked → send-fanout flagged → composition refused (fail closed).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import Send as S\n"
        "def route(_s):\n"
        "    return [S('sub', {}), S('sub', {})]\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('r', lambda s: s)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_conditional_edges('r', route)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:send-fanout"   # aliased Send now detected → fan-out blocks → no number
    assert "bound_factor" not in r


def test_e2e_cursor_param_default_compile_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-36: a compiled subgraph hidden in a parameter DEFAULT
    # `def attach(x=inner.compile()): outer.add_node('sub', x)` → x wasn't tracked → flat undercount (1).
    # Parameter defaults are now paired with their param name and flagged → compose → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "def attach(x=inner.compile()):\n"
        "    outer.add_node('sub', x)\n"
        "attach()\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 1
    assert "bound_factor" not in r


def test_e2e_cursor_match_capture_alias_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-35 (independent re-confirm): a structural-pattern-match capture
    # `match inner.compile(): case c: outer.add_node('sub', c)` binds `c` to the compiled subgraph, a form
    # the prepass didn't track → flat undercount (1). Match-case captures of a compile-containing subject are
    # now flagged → compose → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "match inner.compile():\n"
        "    case c:\n"
        "        outer.add_node('sub', c)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 1
    assert "bound_factor" not in r


def test_e2e_cursor_attribute_stored_alias_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-34 WITNESS: a compiled subgraph stored in an attribute and passed
    # as `outer.add_node('sub', holder.c)`. The arg is an Attribute (not a Name, no inline compile) so it
    # wasn't flagged → flat undercount (10). The add_node arg detection now flags an arg that Load-references
    # a compiled var ANYWHERE (incl. `holder.c` where holder is compiled) → compose → fail closed.
    src = (
        "from types import SimpleNamespace\n"
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "compiled = inner.compile()\n"
        "holder = SimpleNamespace(c=compiled)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', holder.c)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 10})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 10
    assert "bound_factor" not in r


def test_e2e_cursor_tuple_unpack_alias_chain_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-33 WITNESS: `compiled = inner.compile(); (alias,) = (compiled,);
    # outer.add_node('sub', alias)`. The alias is bound via a tuple-unpack of a Name (r31 only handled
    # `alias = compiled`). Name-alias propagation now covers element-wise tuple/list unpack → flagged →
    # compose → fail closed, never the flat undercount (10).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "compiled = inner.compile()\n"
        "(alias,) = (compiled,)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', alias)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 10})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the flat undercount 10
    assert "bound_factor" not in r


def test_e2e_cursor_subgraph_inherits_parent_limit_not_standalone(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-30 WITNESS (CORE MODEL fix): inner's standalone
    # `inner.compile().invoke(recursion_limit=1)` is a SEPARATE run and does NOT constrain inner's execution
    # as outer's subgraph — that inherits the parent run's config. The old model used inner's standalone 1 →
    # composed 100 (understatement). inner now inherits max(outer 50, default 1000) = 1000 → 50 × (1 + 1000)
    # = 50050, default_dependent.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 1})\n"   # standalone low limit — IRRELEVANT
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 50 * (1 + 1000)   # 50050 — inner inherits 1000, NOT its standalone 1 (not 100)
    assert r["bound_factor"] > 100


def test_e2e_cursor_compiled_alias_escapes_to_helper_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-25 WITNESS: `def run(x): x.invoke(config={recursion_limit:5000});
    # run(app)`. The compiled alias `app` is passed to a helper that invokes it via a param → outer's real
    # limit (5000) is out of view → defaulted to 1000 → composed 2000 (true 10000). A compiled alias that
    # escapes (passed as a value) → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def run(x):\n"
        "    x.invoke({}, config={'recursion_limit': 5000})\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 1})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "run(app)\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the defaulted undercount 2000
    assert "bound_factor" not in r


def test_e2e_cursor_negative_recursion_limit_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-24 WITNESS: `config={'recursion_limit': -1}` produced a nonsensical
    # NEGATIVE composed bound (-4). A valid recursion_limit is a positive int; anything else → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 3})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': -1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT a negative bound
    assert "bound_factor" not in r


def test_e2e_cursor_batch_invoke_limit_counted(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-23 WITNESS: `app.batch([{}], config={'recursion_limit': 5000})` was
    # ignored (only invoke/ainvoke/stream/astream were modeled) → max stayed at 50 → composed 50050. batch /
    # abatch must contribute to the limit max like any invocation.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "app.invoke({}, config={'recursion_limit': 50})\n"
        "app.batch([{}], config={'recursion_limit': 5000})\n"
    )
    r = _check_file(tmp_path, src)
    # max(50, 5000) = 5000; inner inherits 5000 → 5000 × (1 + 5000) = 25,005,000 (not 50050)
    assert r["bound_factor"] == 5000 * (1 + 5000)
    assert r["bound_factor"] > 50050


def test_e2e_cursor_with_config_alias_is_non_certifiable(tmp_path):
    # generalization (Cursor r13): an unrecognized method on a compiled-graph alias (`app.with_config({...})`
    # binds a recursion_limit we don't read) → unresolved → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "app2 = app.with_config({'recursion_limit': 5000})\n"
        "app2.invoke({})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"
    assert "bound_factor" not in r


def test_e2e_cursor_mixed_explicit_and_default_invoke_uses_default(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-22 WITNESS: outer invoked twice — once with explicit
    # recursion_limit 50, once with NO config (runtime default 1000). MAX-over-explicit gave 50 → composed
    # 50050, but the no-config run is the worst case (1000). A no-config / no-recursion_limit invoke must
    # contribute the default to the max.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "app.invoke({}, config={'recursion_limit': 50})\n"
        "app.invoke({})\n"
    )
    r = _check_file(tmp_path, src)
    # worst run is the no-config one (default 1000); inner inherits 1000 → 1000 × (1 + 1000) = 1,001,000
    assert r["bound_factor"] == 1000 * (1 + 1000)
    assert r["bound_factor"] > 50050
    # supersteps must reflect the EFFECTIVE limit (1000), consistent with the bound (Cursor r32), NOT 50.
    assert r["supersteps"] == 1000


def test_e2e_cursor_starred_invoke_args_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-20 WITNESS: `payload = ({}, {'recursion_limit': 5000});
    # app.invoke(*payload)` hides config behind a star-unpack → positional read sees no config → defaulted to
    # 1000 → composed 1,001,000 (understated). A *args / **kwargs spread on invoke → unresolved → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "payload = ({}, {'recursion_limit': 5000})\n"
        "app.invoke(*payload)\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the defaulted undercount
    assert "bound_factor" not in r


def test_e2e_cursor_positional_config_is_read(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-17 WITNESS: config passed POSITIONALLY `app.invoke({}, {...})`
    # (not config=) was ignored → defaulted to 1000 → composed bound 1,001,000, understating the true 5000
    # limit. Reading the 2nd positional arg now composes the correct (large) ceiling, not the undercount.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "app.invoke({}, {'recursion_limit': 5000})\n"
    )
    r = _check_file(tmp_path, src)
    # outer explicit 5000; inner inherits 5000 → 5000 × (n_total 1 + inner 5000×1) = 25,005,000 (not 1,001,000)
    assert r["bound_factor"] == 5000 * (1 + 5000)
    assert r["bound_factor"] > 1_001_000


def test_e2e_cursor_named_config_dict_is_non_certifiable(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-16 WITNESS: recursion_limit carried via a NAMED dict
    # (`cfg = {"recursion_limit": 5000}; app.invoke(config=cfg)`) is not read by the inline-dict path →
    # defaulted to 1000 → composed bound understates the true 5000 limit. A non-literal config → unresolved
    # → fail closed.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i', lambda s: s)\n"
        "inner.add_edge(START, 'i'); inner.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "cfg = {'recursion_limit': 5000}\n"
        "app.invoke({}, config=cfg)\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node"   # fail closed, NOT the defaulted-1000 undercount
    assert "bound_factor" not in r


def test_e2e_cursor_multi_invoke_uses_max_limit(tmp_path):
    # audit-3 Cursor gpt-5.3-codex round-15 WITNESS: the outer graph is invoked at TWO call sites with
    # recursion_limit 100 and 5. Each invoke is a separate run; the per-run worst case is the MAX (100).
    # last-wins stored 5 → composed bound 20 vs true 400. invoke_limit must aggregate as max.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def nfn(s): return s\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('i1', nfn)\n"
        "inner.add_edge(START, 'i1'); inner.add_edge('i1', END)\n"
        "inner.compile().invoke({}, config={'recursion_limit': 2})\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('a', nfn)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'a'); outer.add_edge('a', 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "app.invoke({}, config={'recursion_limit': 100})\n"
        "app.invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 100 * (2 + 1000)   # max(100,5)=100 × (n_total 2 + inner inherits 1000); inner standalone 2 ignored (r30)


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


def test_e2e_local_run_in_one_scope_composes(tmp_path):
    # a subgraph built, mutated AND invoked entirely inside ONE function (a local "run") is fully visible →
    # composes. Guards against the scope-split / escape rules over-rejecting the legit single-scope case.
    # (Note: a factory that RETURNS the compiled graph fails closed — the caller's invoke limit is out of
    # view — which is correct; here the graph is invoked locally with a known limit and never escapes.)
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def run():\n"
        "    inner = StateGraph(dict)\n"
        "    inner.add_node('a', lambda s: s)\n"
        "    inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "    inner.compile().invoke({}, config={'recursion_limit': 25})\n"
        "    outer = StateGraph(dict)\n"
        "    outer.add_node('sub', inner.compile())\n"
        "    outer.add_node('b', lambda s: s)\n"
        "    outer.add_edge(START, 'sub'); outer.add_edge('sub', 'b'); outer.add_edge('b', END)\n"
        "    outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default"
    assert r["bound_factor"] == 50 * (2 + 1000)   # inner inherits 1000 (Cursor r30); single-scope local run composes


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
