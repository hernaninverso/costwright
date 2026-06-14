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


def _check_kind(tmp_path, src, kind):
    f = tmp_path / "g.py"
    f.write_text(src, encoding="utf-8")
    meta = {"unit_id": "u", "kind": kind, "file": "g.py"}
    return map_unit(extract_unit(tmp_path, meta), meta)


def test_e2e_invoke_kwargs_spread_fails_closed(tmp_path):
    # audit-3 codex/Cursor r79: a **kwargs spread on an invoke/run call is OPAQUE — it could carry a
    # max_turns/recursion_limit that DISABLES the cap (e.g. Runner.run(a, **{"max_turns": None})), and the
    # bound would be unrecoverable. _scan_invoke ignored the spread (keyword.arg is None) so the analyzer fell
    # back to the framework default and CERTIFIED a finite bound (false assurance). Now it fails closed.
    cases = (
        ('from agents import Agent, Runner\nRunner.run(Agent(name="x"), input="x", **{"max_turns": None})\n', "agents_sdk"),
        ('from agents import Agent, Runner\nopts={"max_turns": None}\nRunner.run_sync(Agent(name="x"), "x", **opts)\n', "agents_sdk"),
        ('from langgraph.graph import StateGraph, START, END\ng=StateGraph(dict)\ng.add_node("a", lambda s: s)\n'
         'g.add_edge(START,"a"); g.add_edge("a",END)\ng.compile().invoke({}, **{"config": {"recursion_limit": 99999}})\n', "langgraph"),
    )
    for src, kind in cases:
        r = _check_kind(tmp_path, src, kind)
        assert r["category"] == "extractor-failure" and "bound_factor" not in r, (kind, r)
    # regression: a normal explicit bound (no spread) still certifies
    ok = _check_kind(tmp_path, 'from agents import Agent, Runner\nRunner.run_sync(Agent(name="x"), "x", max_turns=5)\n', "agents_sdk")
    assert ok["category"] == "tipa:explicit" and ok["bound_factor"] == 5, ok


def test_e2e_crewai_hierarchical_alias_fails_closed(tmp_path):
    # audit-3 codex r75: a CrewAI hierarchical process runs a MANAGER that re-delegates (unbounded). The
    # detection caught the literal `process=Process.hierarchical` but NOT an aliased `mode =
    # Process.hierarchical; process=mode` (enum or string) — those certified a finite bound (false assurance).
    # Now only a confirmed-sequential LITERAL is safe; a hierarchical literal/alias/variable fails closed.
    blocked = (
        "Crew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)], process=Process.hierarchical)",
        "mode = Process.hierarchical\nCrew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)], process=mode)",
        "mode = 'hierarchical'\nCrew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)], process=mode)",
        "Crew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)], process='hierarchical')",
    )
    for body in blocked:
        src = "from crewai import Crew, Agent, Process\n" + body + "\n"
        r = _check_kind(tmp_path, src, "crewai")
        assert r["category"] == "no-mapeable:hierarchical-manager" and "bound_factor" not in r, (body, r)

    # sequential (literal, string, or default) is NOT a manager loop ⇒ certifiable
    for body in (
        "Crew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)], process=Process.sequential)",
        "Crew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)], process='sequential')",
        "Crew(agents=[Agent(role='r', goal='g', backstory='b', max_iter=2)])",
    ):
        src = "from crewai import Crew, Agent, Process\n" + body + "\n"
        r = _check_kind(tmp_path, src, "crewai")
        assert r["category"] == "tipa:explicit" and r["bound_factor"] == 2, (body, r)


def test_e2e_multiple_explicit_bounds_combine(tmp_path):
    # audit-3 codex r70: MULTIPLE explicit bounds of the same param were collapsed to the FIRST → understated.
    # LangGraph invokes are separate runs ⇒ per-run worst case = MAX recursion_limit. CrewAI agents / Agents-SDK
    # handoffs are sequential ⇒ SUM of iteration budgets.
    lg = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', END)\n"
        "app = g.compile()\n"
        "app.invoke({}, config={'recursion_limit': 5})\n"
        "app.invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_kind(tmp_path, lg, "langgraph")
    assert r["bound_factor"] == 50, r          # MAX(5, 50), not the first (5)

    crew = (
        "from crewai import Agent\n"
        "a1 = Agent(role='r1', goal='g', backstory='b', max_iter=1)\n"
        "a2 = Agent(role='r2', goal='g', backstory='b', max_iter=50000)\n"
    )
    rc = _check_kind(tmp_path, crew, "crewai")
    assert rc["bound_factor"] == 50001, rc     # SUM(1, 50000), not the first (1)


def test_e2e_flat_node_helper_multicall_fails_closed(tmp_path):
    # audit-3 Cursor r71 (flat path, inter-procedural): a function that adds nodes — `def add(name):
    # g.add_node(name, n)` — called MULTIPLE times (`add('a'); add('b'); add('c')`) or inside a loop
    # materializes N runtime nodes from ONE textual site → the flat count undercounts. Now a node-adding helper
    # called >=2 times or in a loop fails closed. A helper called exactly once is counted correctly.
    multicall = (
        "from langgraph.graph import StateGraph, START\n"
        "g = StateGraph(dict)\n"
        "g.add_node('seed', lambda s: s)\n"
        "def add(name):\n    g.add_node(name, lambda s: s)\n"
        "add('a'); add('b'); add('c')\n"
        "g.add_edge(START, 'seed')\n"
        "g.compile().invoke({}, config={'recursion_limit': 10})\n"
    )
    r = _check_file(tmp_path, multicall)
    assert "bound_factor" not in r, r
    assert r["category"] in ("no-mapeable:node-helper-multicall", "extractor-failure"), r

    in_loop = (
        "from langgraph.graph import StateGraph, START\n"
        "g = StateGraph(dict)\n"
        "def add(name):\n    g.add_node(name, lambda s: s)\n"
        "for x in ['a', 'b', 'c']:\n    add(x)\n"
        "g.add_edge(START, 'a')\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    rl = _check_file(tmp_path, in_loop)
    assert "bound_factor" not in rl, rl

    # COVERAGE: a node-adding helper called exactly ONCE is counted correctly.
    once = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def build(g):\n    g.add_node('a', lambda s: s)\n    g.add_node('b', lambda s: s)\n"
        "g = StateGraph(dict)\n"
        "build(g)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', 'b'); g.add_edge('b', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    ro = _check_file(tmp_path, once)
    assert ro["category"] == "tipa:explicit" and ro["bound_factor"] == 5, ro


def test_e2e_flat_nonpositive_recursion_limit_fails_closed(tmp_path):
    # audit-3 r69 (flat path): an explicit recursion_limit <= 0 yielded a zero/NEGATIVE ceiling (bf=-5 for
    # recursion_limit=-5), which is nonsensical and understates any real run. The framework rejects <1 at
    # runtime; costwright now fails closed (extractor-failure: nonpositive-bound). The valid minimum (1) still
    # certifies.
    base = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', END)\n"
    )
    for lim in (0, -5):
        r = _check_file(tmp_path, base + f"g.compile().invoke({{}}, config={{'recursion_limit': {lim}}})\n")
        assert r["category"] == "extractor-failure" and "bound_factor" not in r, (lim, r)
    r1 = _check_file(tmp_path, base + "g.compile().invoke({}, config={'recursion_limit': 1})\n")
    assert r1["category"] == "tipa:explicit" and r1["bound_factor"] == 1, r1


def test_e2e_flat_add_node_in_loop_fails_closed(tmp_path):
    # audit-3 codex r68 (flat path): add_node inside a for/while/comprehension builds N runtime nodes from ONE
    # textual site — `for i in range(100): g.add_node('leaf'+str(i), ...)` — so the static node count undercounts
    # (n_nodes counted the site once). The subgraph path failed closed on loop-built graphs; the FLAT path now
    # tracks loop depth and flags node-in-loop too.
    cases = (
        "for i in range(100):\n    g.add_node('leaf' + str(i), lambda s: s)\n",
        "[g.add_node('n' + str(i), lambda s: s) for i in range(50)]\n",
        "i = 0\nwhile i < 10:\n    g.add_node('n' + str(i), lambda s: s)\n    i += 1\n",
    )
    for loop in cases:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "g = StateGraph(dict)\n"
            "g.add_node('mid', lambda s: s)\n"
            + loop +
            "g.add_edge(START, 'mid')\n"
            "g.compile().invoke({}, config={'recursion_limit': 3})\n"
        )
        r = _check_file(tmp_path, src)
        assert "bound_factor" not in r, (loop, r)
        assert r["category"].startswith("no-mapeable:") or r["category"] == "extractor-failure", (loop, r)

    # a loop that does NOT add_node must not block
    ok = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s); g.add_node('b', lambda s: s)\n"
        "for x in ['a', 'b']:\n    pass\n"
        "g.add_edge(START, 'a'); g.add_edge('a', 'b'); g.add_edge('b', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, ok)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_flat_retry_policy_fails_closed(tmp_path):
    # audit-3 codex r67 (flat path): a node with a RetryPolicy(max_attempts=k) runs up to k× per super-step, so
    # the flat bound (recursion_limit × n_nodes, retry=1) understates. The subgraph path already fails closed on
    # retry; the FLAT path must too. `retry=`, `retry_policy=`, `error_handler=`, `**kwargs`, and graph-wide
    # set_node_defaults now flag node-unmodeled-retry → fail closed.
    cases = (
        "g.add_node('a', lambda s: s, retry=object())\n",
        "g.add_node('a', lambda s: s, retry_policy=object())\n",
        "g.add_node('a', lambda s: s, error_handler='h')\n",
        "opts = {'retry': 1}\ng.add_node('a', lambda s: s, **opts)\n",
        "g.set_node_defaults(retry_policy=object())\ng.add_node('a', lambda s: s)\n",
    )
    for stmt in cases:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "g = StateGraph(dict)\n"
            + stmt +
            "g.add_edge(START, 'a'); g.add_edge('a', END)\n"
            "g.compile().invoke({}, config={'recursion_limit': 3})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:node-unmodeled-retry", (stmt, r)
        assert "bound_factor" not in r

    # a NON-retry kwarg (metadata) must NOT block
    ok = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s, metadata={'x': 1})\n"
        "g.add_edge(START, 'a'); g.add_edge('a', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, ok)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_flat_add_sequence_counts_nodes(tmp_path):
    # audit-3 codex r65 (flat path): `g.add_sequence([(name, action), ...])` adds ONE node per element; the flat
    # extractor counted ZERO → understated. Now a literal sequence is counted element-by-element. With a
    # conditional edge (cyclic) the bound is supersteps × n_nodes.
    seq = "[" + ", ".join(f"('n{i}', lambda s: s)" for i in range(20)) + "]"
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        f"g.add_sequence({seq})\n"
        "g.add_edge(START, 'n0')\n"
        "g.add_conditional_edges('n0', lambda s: 'n1', {'n1': 'n1'})\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["bound_factor"] is not None and r["bound_factor"] >= 20, r   # not the old n_nodes=0 → bf=5

    # a non-literal add_sequence (unknown node count) fails closed
    dyn = (
        "from langgraph.graph import StateGraph, START, END\n"
        "nodes = [('a', lambda s: s)]\n"
        "g = StateGraph(dict)\n"
        "g.add_sequence(nodes)\n"
        "g.add_edge(START, 'a')\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    rd = _check_file(tmp_path, dyn)
    assert rd["category"] == "no-mapeable:add-sequence-dynamic" and "bound_factor" not in rd, rd


def test_e2e_flat_fanout_from_start_is_not_understated(tmp_path):
    # audit-3 codex r65 (flat path): the `linear` optimization (bound = supersteps, 1 node/super-step) is unsound
    # when the graph FANS OUT — START→n0..n9 all activate in super-step 1, so true per-run ≥ 10 (with rl=3). The
    # old all-static heuristic wrongly classed it linear → bf=3. Now any source with ≥2 static successors makes
    # the bound supersteps × n_nodes.
    nodes = "\n".join(f"g.add_node('n{i}', lambda s: s)" for i in range(10))
    edges = "\n".join(f"g.add_edge(START, 'n{i}'); g.add_edge('n{i}', END)" for i in range(10))
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        f"{nodes}\n{edges}\n"
        "g.compile().invoke({}, config={'recursion_limit': 3})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["bound_factor"] is not None and r["bound_factor"] >= 10, r   # 3 × 10 = 30, not the old 3


def test_e2e_flat_true_chain_stays_linear(tmp_path):
    # COVERAGE guard: a true chain (each node exactly one successor) keeps the tight linear bound = supersteps.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s); g.add_node('b', lambda s: s); g.add_node('c', lambda s: s)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', 'b'); g.add_edge('b', 'c'); g.add_edge('c', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["bound_factor"] == 5, r   # linear: supersteps, not 5×3


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
    # fail closed either way: the flat loop guard (node-in-loop) may fire before compose, or the composition
    # path fails closed on the loop-built graph (subgraph-node) — both sound (no number).
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-in-loop"), r
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
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-in-loop"), r
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


def test_e2e_bound_method_addnode_alias_fails_closed(tmp_path):
    # audit-3 codex r63 WITNESS: `add = outer.add_node; add("s", inner.compile())`. The FLAT extractor matched
    # add_node by the call name's last segment, so a bound-method alias `add(...)` (last="add") was NOT
    # recognized as add_node at all → the subgraph arg was never scanned and the node never counted → bf
    # understated. Now names bound to `<g>.add_node` (directly, via alias chain, or functools.partial) are
    # tracked and a call to them is treated as add_node → the compiled arg flags subgraph-node → fail closed.
    cases = (
        "add = outer.add_node\nadd('s', inner.compile())\n",
        "import functools\nadd = functools.partial(outer.add_node)\nadd('s', inner.compile())\n",
        "add = outer.add_node\nadd2 = add\nadd2('s', inner.compile())\n",
    )
    for stash in cases:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('a', lambda s: s)\n"
            "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            "outer = StateGraph(dict)\n"
            + stash +
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (stash, r)
        assert "bound_factor" not in r


def test_e2e_construct_passed_as_arg_fails_closed(tmp_path):
    # audit-3 Cursor r66 WITNESS: a Send/Command/interrupt invoked through a HIGHER-ORDER function —
    # `idfn(Send)("s1", {})` (idfn returns its arg), `functools.partial(Send)(...)`, `sends.append(Send);
    # sends[0](...)` — hides the fan-out (no send-fanout feature) so composition emitted a finite bound. Now any
    # construct alias used as a call ARGUMENT (not the callee) fails closed (no-mapeable:construct-escaped).
    cases = (
        "def idfn(x):\n    return x\ndef route(s):\n    return [idfn(Send)('sub', {}), idfn(Send)('sub', {})]\n",
        "import functools\ndef route(s):\n    return [functools.partial(Send)('sub', {})]\n",
        "sends = []\nsends.append(Send)\ndef route(s):\n    return [sends[0]('sub', {})]\n",
    )
    for defs in cases:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "from langgraph.types import Send\n"
            + defs +
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
        assert r["category"] == "no-mapeable:construct-escaped", (defs, r)
        assert "bound_factor" not in r


def test_e2e_direct_send_still_send_fanout(tmp_path):
    # COVERAGE guard: a directly-called Send `Send(...)` (and bare-Name aliases) must STILL report send-fanout,
    # not the broader construct-escaped — the construct-escape guard only fires on argument-position uses.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "from langgraph.types import Send\n"
        "def route(s):\n    return [Send('sub', {}), Send('sub', {})]\n"
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


def test_e2e_obscured_addnode_call_fails_closed(tmp_path):
    # audit-3 codex r63 (escape guard): the add_node CALL itself can be obscured so node-counting silently
    # undercounts — `g.add_node` captured into a container/arg/attribute or reached via getattr. The r63 alias
    # recognition only covers assignments to a bare Name (add = g.add_node, partial, alias chains); ANY OTHER
    # capture of `.add_node` as a value, or getattr(_, "add_node"), now fails closed (no-mapeable:addnode-escaped).
    obscured = (
        'getattr(outer, "add_node")("s", inner.compile())\n',
        'm = {"add": outer.add_node}\nm["add"]("s", inner.compile())\n',
        'fns = [outer.add_node]\nfns[0]("s", inner.compile())\n',
        'def reg(fn):\n    fn("s", inner.compile())\nreg(outer.add_node)\n',
    )
    for call in obscured:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('a', lambda s: s)\n"
            "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            "outer = StateGraph(dict)\n"
            + call +
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:addnode-escaped", (call, r)
        assert "bound_factor" not in r


def test_e2e_direct_addnode_not_flagged_escaped(tmp_path):
    # COVERAGE guard: ordinary direct add_node calls (and the r63 bare-Name aliases) must NOT trip the escape
    # guard — a normal composing graph still composes, a plain flat graph stays flat.
    compose_src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, compose_src)
    assert r["category"] == "tipa:framework-default" and r["bound_factor"] == 50 * (1 + 1000), r
    flat_src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s)\n"
        "g.add_node('b', lambda s: s)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', 'b'); g.add_edge('b', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r2 = _check_file(tmp_path, flat_src)
    assert r2["category"] == "tipa:explicit" and r2["bound_factor"] == 5, r2


def test_e2e_plain_function_named_add_is_not_addnode(tmp_path):
    # COVERAGE guard: a plain local function that happens to be named `add` (not bound to g.add_node) must NOT
    # be treated as an add_node alias.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def add(x, y):\n    return x + y\n"
        "add(1, 2)\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_classvar_and_decorator_subgraph_fail_closed(tmp_path):
    # audit-3 r61: two further same-file shapes — (a) a class attribute holding a compiled subgraph
    # `class C: sub = inner.compile()` accessed `C.sub`; (b) a function whose DECORATOR returns a compiled
    # subgraph `@deco def node` (node becomes the subgraph). Both were flat-counted; now both fail closed.
    classvar = (
        "class C:\n    sub = inner.compile()\n",
        "C.sub")
    decorator = (
        "def deco(f):\n    return inner.compile()\n@deco\ndef node():\n    pass\n",
        "node")
    for defs, action in (classvar, decorator):
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('a', lambda s: s)\n"
            "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            + defs +
            "outer = StateGraph(dict)\n"
            f"outer.add_node('s', {action})\n"
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (action, r)
        assert "bound_factor" not in r


def test_e2e_plain_classvar_and_decorator_do_not_falsely_block(tmp_path):
    # COVERAGE guard: a plain class attr and a plain (identity/timing) decorator must NOT taint — the real
    # subgraph still composes and the plain decorated node stays a normal node.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "class C:\n    x = 5\n"
        "def timing(f):\n    return f\n"
        "@timing\ndef mynode(s):\n    return s\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('s', inner.compile())\n"
        "outer.add_node('n', mynode)\n"
        "outer.add_edge(START, 's'); outer.add_edge('s', 'n'); outer.add_edge('n', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default" and r["bound_factor"] == 5 * (2 + 1000), r


def test_e2e_subgraph_passed_as_function_arg_fails_closed(tmp_path):
    # audit-3 r60: a compiled subgraph passed INTO a function as an argument — `def use(sub): outer.add_node(
    # "s", sub)` called `use(inner.compile())` — was flat-counted (no inter-procedural arg flow). Now a function
    # called with a compiled subgraph as any arg taints its parameters, so `add_node("s", sub)` fails closed.
    for call in ("use(inner.compile())", "use(sub=inner.compile())"):
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "def use(sub=None):\n"
            "    outer = StateGraph(dict)\n"
            "    outer.add_node('s', sub)\n"
            "    outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "    return outer.compile()\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('a', lambda s: s)\n"
            "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            f"{call}.invoke({{}}, config={{'recursion_limit': 5}})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (call, r)
        assert "bound_factor" not in r


def test_e2e_function_called_without_subgraph_still_flat(tmp_path):
    # COVERAGE guard: a function called with only NON-subgraph args must not taint its params.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def helper(x):\n"
        "    return x\n"
        "helper(5)\n"
        "g = StateGraph(dict)\n"
        "g.add_node('a', lambda s: s)\n"
        "g.add_edge(START, 'a'); g.add_edge('a', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_more_indirection_shapes_fail_closed(tmp_path):
    # audit-3 codex r59: three further same-file indirections that were flat-counted — now all fail closed.
    cases = {
        # (1) augmented assignment `subs += [c]` (AugAssign was not in the binding loop)
        "augassign": (
            "subs = []\nsubs += [inner.compile()]\n", "subs[0]"),
        # (2) a generator that YIELDS a compiled subgraph (factory detection looked at return, not yield)
        "generator": (
            "def gen():\n    yield make_inner()\nsub = next(gen())\n", "sub"),
        # (3) setattr(obj, 'x', compiled) then getattr(obj, 'x') (dynamic attribute stash/read)
        "setattr": (
            "class H: pass\nh = H()\nsetattr(h, 'sub', inner.compile())\n", "getattr(h, 'sub')"),
    }
    base = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def make_inner():\n"
        "    g = StateGraph(dict)\n"
        "    g.add_node('a', lambda s: s)\n"
        "    g.add_edge(START, 'a'); g.add_edge('a', END)\n"
        "    return g.compile()\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
    )
    for name, (stash, action) in cases.items():
        src = (
            base + stash +
            "outer = StateGraph(dict)\n"
            f"outer.add_node('s', {action})\n"
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (name, r)
        assert "bound_factor" not in r


def test_e2e_compiled_subgraph_stashed_via_method_fails_closed(tmp_path):
    # audit-3 codex/Cursor r57: a compiled subgraph stashed into a container via a METHOD call —
    # `lst.append(inner.compile())`, `reg.add(c)`, `d.update({"k": c})` — then read back as an add_node action.
    # The subscript-assign taint (r55) missed method mutation, so it flat-counted. Now a compiled subgraph
    # passed as an arg to any non-graph method taints the receiver → the later subscript add_node arg is
    # flagged → fail closed.
    cases = (
        ("lst = []\nlst.append(inner.compile())\n", "lst[0]"),
        ("d = {}\nd.update({'k': inner.compile()})\n", "d['k']"),
    )
    for stash, action in cases:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('a', lambda s: s)\n"
            "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            + stash +
            "outer = StateGraph(dict)\n"
            f"outer.add_node('s', {action})\n"
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (stash, r)
        assert "bound_factor" not in r


def test_e2e_invoke_method_does_not_taint_host(tmp_path):
    # COVERAGE guard: the graph-build/run methods (add_node/.../invoke/batch) must NOT taint their receiver —
    # `app.invoke({}, config=...)` passes inputs, not a stashed subgraph. The aliased subgraph still composes.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "ci = inner.compile()\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', ci)\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "app = outer.compile()\n"
        "app.invoke({}, config={'recursion_limit': 50})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:framework-default" and r["bound_factor"] == 50 * (1 + 1000), r


def test_e2e_subgraph_factory_function_fails_closed(tmp_path):
    # audit-3 codex/Cursor r56: a function that RETURNS a compiled subgraph is a factory —
    # `def make(): ...; return inner.compile()` then `outer.add_node("s", make())`. The compiled-var tracker
    # followed only value-bindings, so the Call `make()` was flat-counted (bf understated 5 vs 5005+). Now a
    # function whose own return carries a compiled subgraph is tainted into compiled_vars, so `make()` is
    # flagged → the Call inner can't be attributed → fail closed. Covers return-inline-compile and return-local.
    for ret in ("    return inner.compile()\n", "    ci = inner.compile()\n    return ci\n"):
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "def make_sub():\n"
            "    inner = StateGraph(dict)\n"
            "    inner.add_node('a', lambda s: s)\n"
            "    inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            + ret +
            "outer = StateGraph(dict)\n"
            "outer.add_node('s', make_sub())\n"
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (ret, r)
        assert "bound_factor" not in r


def test_e2e_method_factory_fails_closed(tmp_path):
    # audit-3 codex r58: a subgraph factory accessed as a METHOD/attribute — `Factory.make()` (staticmethod),
    # `obj.make()` (instance), `self.build_inner()` — returns a compiled subgraph. The factory name `make`/
    # `build_inner` is tainted, but the CALL is an Attribute access (`Factory.make`), not a bare Name, so the
    # arg-Load-reference check missed it → flat-counted. Now the add_node arg scan also matches a Call whose
    # callee is an Attribute whose .attr is a known factory name → flagged → fail closed.
    cases = (
        "class Factory:\n    @staticmethod\n    def make():\n        inner = StateGraph(dict)\n"
        "        inner.add_node('a', lambda s: s)\n        inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "        return inner.compile()\nouter = StateGraph(dict)\nouter.add_node('sub', Factory.make())\n",
        "class B:\n    def make(self):\n        inner = StateGraph(dict)\n"
        "        inner.add_node('a', lambda s: s)\n        inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
        "        return inner.compile()\nb = B()\nouter = StateGraph(dict)\nouter.add_node('sub', b.make())\n",
    )
    for head in cases:
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            + head +
            "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (head, r)
        assert "bound_factor" not in r


def test_e2e_plain_method_factory_still_flat(tmp_path):
    # COVERAGE guard: a method that returns a PLAIN callable (not a subgraph) must NOT be treated as a factory.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "class H:\n"
        "    def fn(self):\n"
        "        return lambda s: s\n"
        "h = H()\n"
        "g = StateGraph(dict)\n"
        "g.add_node('x', h.fn())\n"
        "g.add_edge(START, 'x'); g.add_edge('x', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_plain_factory_function_still_flat(tmp_path):
    # COVERAGE guard: a factory that returns a PLAIN callable (no compiled subgraph) must NOT taint — the
    # node is normal and the flat path applies.
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "def make_fn():\n"
        "    return lambda s: s\n"
        "g = StateGraph(dict)\n"
        "g.add_node('x', make_fn())\n"
        "g.add_edge(START, 'x'); g.add_edge('x', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_compiled_subgraph_in_container_fails_closed(tmp_path):
    # audit-3 codex r55 (also surfaced in my own probe): a compiled subgraph stashed in a CONTAINER value —
    # `d["k"] = inner.compile()` (subscript target) — then used as an add_node action `outer.add_node("s",
    # d["k"])`. The compiled-var tracker only followed Name bindings, so `d["k"]` was not recognized as a
    # subgraph → the flat path counted "s" as ONE ordinary node → bf understated (5 instead of 5005+). Now the
    # container base name `d` is tainted into compiled_vars, so the add_node arg Load-references it → flagged →
    # the subscript inner can't be attributed → completeness guard fails closed.
    for stash in ("d = {}\nd['k'] = inner.compile()\n", "class R: pass\nr = R()\nr.sub = inner.compile()\n"):
        action = "d['k']" if "d['k']" in stash else "r.sub"
        src = (
            "from langgraph.graph import StateGraph, START, END\n"
            "inner = StateGraph(dict)\n"
            "inner.add_node('a', lambda s: s)\n"
            "inner.add_edge(START, 'a'); inner.add_edge('a', END)\n"
            + stash +
            "outer = StateGraph(dict)\n"
            f"outer.add_node('s', {action})\n"
            "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
            "outer.compile().invoke({}, config={'recursion_limit': 5})\n"
        )
        r = _check_file(tmp_path, src)
        assert r["category"] == "no-mapeable:subgraph-node", (stash, r)
        assert "bound_factor" not in r


def test_e2e_plain_container_action_still_flat(tmp_path):
    # COVERAGE guard: a container that holds a PLAIN function (never a compiled subgraph) must NOT taint — the
    # node is a normal node and the flat path applies. `d['fn'] = lambda` then add_node('x', d['fn']).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "d = {}\n"
        "d['fn'] = lambda s: s\n"
        "g = StateGraph(dict)\n"
        "g.add_node('x', d['fn'])\n"
        "g.add_edge(START, 'x'); g.add_edge('x', END)\n"
        "g.compile().invoke({}, config={'recursion_limit': 5})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "tipa:explicit" and r["bound_factor"] == 5, r


def test_e2e_mixed_attributed_and_unattributed_subgraph_fails_closed(tmp_path):
    # audit-3 codex r53 WITNESS: a file with TWO compiled-subgraph add_node sites — one attributable
    # (`outer.add_node("s", small.compile())`) and one NOT (`box[0].add_node("huge", big.compile())`, subscript
    # receiver). The per-graph analyzer attributes only the first, so composing just `outer` emits a confident
    # number (1001) while the second compiled subgraph — which could run a far larger inner — is INVISIBLE to the
    # analysis → undercount of the file. Now: #subgraph-node features (2) > #attributed subgraph_nodes (1) ⇒ fail
    # closed (never a number when a compiled subgraph escapes attribution).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "small = StateGraph(dict)\n"
        "small.add_node('i', lambda s: s)\n"
        "small.add_edge(START, 'i'); small.add_edge('i', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('s', small.compile())\n"
        "outer.add_edge(START, 's'); outer.add_edge('s', END)\n"
        "box = [StateGraph(dict)]\n"
        "box[0].add_node('huge', small.compile())\n"     # unattributable subscript receiver
        "box[0].add_edge(START, 'huge'); box[0].add_edge('huge', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 1})\n"
    )
    r = _check_file(tmp_path, src)
    assert r["category"] == "no-mapeable:subgraph-node", r
    assert "bound_factor" not in r


def test_e2e_batch_host_is_per_run_not_aggregate(tmp_path):
    # audit-3 Cursor r50 filed a "batch([6000]) totals 6000 > per-run 5005 ⇒ understate" BLOCKER. codex
    # adjudicated it a per-run-vs-aggregate SEMANTIC false positive: the certificate's node_executions_ceiling
    # is PER RUN (recursion_limit is per-run). `batch([N])` runs the graph N INDEPENDENT times, each bounded by
    # the same per-run ceiling — identical to calling invoke in a loop, which costwright also reports per-run.
    # The aggregate is (per-run)×(cardinality), outside the per-run metric. This pins: a batch host reports the
    # SAME per-run ceiling as invoke, NOT a number multiplied by the batch length. (Multiplying would make the
    # metric depend on calling syntax and diverge from the flat path; codex recommended against it.)
    inner = "inner = StateGraph(dict)\ninner.add_node('a', lambda s: s)\ninner.add_edge(START,'a'); inner.add_edge('a',END)\n"
    outer = ("outer = StateGraph(dict)\nouter.add_node('sub', inner.compile())\n"
             "outer.add_edge(START,'sub'); outer.add_edge('sub',END)\napp = outer.compile()\n")
    head = "from langgraph.graph import StateGraph, START, END\n" + inner + outer
    r_invoke = _check_file(tmp_path, head + "app.invoke({}, config={'recursion_limit': 5})\n")
    r_batch = _check_file(tmp_path, head + "app.batch([{} for _ in range(6000)], config={'recursion_limit': 5})\n")
    # both report the SAME per-run ceiling 5 × (1 + 1000) = 5005 — batch cardinality is not folded in.
    assert r_invoke["bound_factor"] == 5 * (1 + 1000), r_invoke
    assert r_batch["bound_factor"] == r_invoke["bound_factor"], (r_batch, r_invoke)


def test_e2e_edge_to_undefined_node_is_not_undercount(tmp_path):
    # audit-3 Cursor r48 raised a "ghost" node referenced only by add_edge (never add_node'd), claiming the
    # inner node count of 1 understates a "true" 2. VERIFIED FALSE POSITIVE against real langgraph 1.x:
    # `compile()` raises `ValueError: Found edge starting at unknown node 'ghost'` — an edge to a node never
    # added via add_node makes the graph INVALID (it never runs), so there is no running graph to understate.
    # Every runnable LangGraph node is created ONLY by add_node (or add_sequence, which fails closed); costwright
    # counting add_node sites is therefore sound. This pins the behavior: the inner has ONE real node, bound is
    # composed on that, and the phantom edge target is correctly ignored (not a node).
    src = (
        "from langgraph.graph import StateGraph, START, END\n"
        "inner = StateGraph(dict)\n"
        "inner.add_node('a', lambda s: s)\n"
        "inner.add_edge(START, 'a')\n"
        "inner.add_edge('a', 'ghost')\n"     # 'ghost' never add_node'd ⇒ invalid graph in real langgraph
        "inner.add_edge('ghost', END)\n"
        "outer = StateGraph(dict)\n"
        "outer.add_node('sub', inner.compile())\n"
        "outer.add_edge(START, 'sub'); outer.add_edge('sub', END)\n"
        "outer.compile().invoke({}, config={'recursion_limit': 2})\n"
    )
    r = _check_file(tmp_path, src)
    # inner has exactly ONE add_node'd node; composed on it (inner inherits 1000). 2 × (1 + 1000×1) = 2002.
    assert r["category"] == "tipa:framework-default" and r["bound_factor"] == 2 * (1 + 1000), r


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
    # the node-adding closure called in a loop trips the flat node-helper-multicall guard before compose; either
    # fail-closed reason is sound (no number).
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-helper-multicall"), r
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
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-in-loop"), r
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
    # loop-built big_graph also trips the flat node-in-loop guard; either fail-closed reason is sound.
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-in-loop"), r
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
    # fail closed either way: the flat retry/kwargs guard (node-unmodeled-retry) may fire before compose, or the
    # composition path absorbs it (subgraph-node) — both are sound (no number).
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-unmodeled-retry"), r
    assert "bound_factor" not in r


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
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-unmodeled-retry"), r
    assert "bound_factor" not in r


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
    assert r["category"] in ("no-mapeable:subgraph-node", "no-mapeable:node-unmodeled-retry"), r
    assert "bound_factor" not in r


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
