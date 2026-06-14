"""costwright.subgraph — sound bound COMPOSITION for nested LangGraph subgraphs (feature 005, v0.2.0).

A subgraph node — `g.add_node("sub", inner.compile())` — runs the inner compiled graph as a
SUB-INVOCATION each time the outer node activates. costwright used to BLOCK these (non_certifiable);
this module composes the bound when it is SOUND to.

SOUNDNESS (council 2026-06-13, which REFUTED the naïve `outer × inner`):
  `outer × inner` is sound ONLY under the NO-FAN-OUT invariant — a single outer super-step must not be
  able to activate the subgraph node more than once. `Send` fan-out breaks it, but `send-fanout` (and
  `dynamic-goto`) are ALREADY BLOCKING features (such a graph is non_certifiable BEFORE composition), and
  absent them LangGraph's BSP model activates a node ≤ once per super-step (conditional edges / loops
  schedule LATER super-steps; they do NOT re-enter within one — codex). So the activation count ≤
  outer_steps, and the bound below holds. compose() RE-ASSERTS no-fan-out and refuses otherwise.

CONSERVATIVE bound (never understates — same discipline as the 004 CP bound):
  bound_factor(G) = outer_steps(G) × ( n_normal_nodes(G) + Σ_subgraph-nodes bound_factor(inner) )
  i.e. each super-step (≤ outer_steps) may run every node once; a normal node costs 1, a subgraph node
  costs its inner bound_factor. Always an upper bound (linear cases are ≤ this).

PROVENANCE is ABSORBING (council P0): non_certifiable / runaway absorb and DROP the numeric ceiling, so
an unbounded inner can never hide behind a small outer. Imported / unresolvable inner ⇒ non_certifiable.
Depth-cap (5) / definition cycle ⇒ non_certifiable (codex: NOT runaway). `runaway` only when a SINGLE
graph's EXPLICIT recursion_limit ≥ HUGE_LIMIT (effectively no limit) — a large COMPOSED product is a
legitimate, sound ceiling (reported), not a runaway.

NAME-SOUNDNESS (audit-3 codex rounds 3-5): a name-keyed map is only a sound resolver when the name has
EXACTLY ONE classifiable binding and is not imported. Otherwise static resolution may pick the wrong
(smaller) graph and UNDERSTATE: an IMPORTED name is an opaque runtime value; a name bound to a compiled
graph MORE THAN ONCE is non-deterministic to us — across scopes (a sibling function or class body) OR within
one scope via control flow (`if/else`, `try`) or rebinding. So `_ambiguous(name)` := imported OR bound >1×;
ambiguous names fail closed (non_certifiable, NO number). Counting bindings (not scopes) is the simplest
rule that dominates every cross-scope AND same-scope witness. A `from X import *` can rebind ANY name to an
unseen value, so it poisons every name in the file (all composition fails closed). LIMITATION (documented, fundamental to static
analysis): a subgraph passed as an OPAQUE value (a bare imported name, or one bound by an unrecognized
pattern like tuple-unpack / for-target) is indistinguishable from an ordinary function node, so it is
counted as ONE outer activation; its internal executions are NOT included — costwright bounds VISIBLE node
activations and composes the inner cost only for subgraphs it can SEE compiled in this file.

PURE STDLIB. Invoked ONLY when the flat extractor already flagged a `subgraph-node` feature — the flat
path (99% of files, no subgraphs) is untouched.
"""
import ast

from costwright.extract import DEFAULTS, call_name, const_of, const_or_endref

DEPTH_CAP = 5
HUGE_LIMIT = 10_000
_FANOUT_FEATURES = {"send-fanout", "dynamic-goto"}   # break the no-fan-out invariant ⇒ do NOT compose


class _GraphReceivers(ast.NodeVisitor):
    """Per-StateGraph-variable collection (nodes/edges per graph var, `app = g.compile()` links, invoke
    recursion_limit, subgraph node→inner-graph map). Composition-only; does not touch flat extraction."""

    def __init__(self):
        self.graphs = {}            # var -> {"nodes":[(name,line)], "edges":[], "unmodeled": str|None}
        self.compiled_from = {}     # app var -> source StateGraph var   (app = g.compile())
        self.invoke_limit = {}      # source-graph var -> int | "unresolved"  (recursion_limit at its invoke)
        self.subgraph_nodes = []    # (outer_var, node_name, inner_graph_var, alias_or_None, line)
        self.pregel_vars = set()    # vars bound to Pregel(...) — unresolvable inner ⇒ non_certifiable
        # AMBIGUITY guard (audit-3 codex). A name used to pass a subgraph can only be resolved soundly by a
        # name-keyed map if it has EXACTLY ONE classifiable binding and is not imported. ANY of these is
        # ambiguous ⇒ fail closed (no number), because static name-resolution may pick the wrong (smaller)
        # graph: IMPORTED (opaque runtime value); bound to a compiled graph MORE THAN ONCE — across scopes
        # (sibling function / class body) OR within one scope via control flow (if/else, try) or rebinding,
        # where the runtime value isn't statically determined. Counting bindings (not distinct scopes)
        # subsumes every cross-scope case AND the same-scope branch/rebind case (codex rounds 3-5).
        self.imported = set()       # names introduced by import / from-import (opaque runtime values)
        self.store_count = {}       # name -> # of binding SITES (any `name = …`); filled by analyze()
        self.star_import = False    # a `from X import *` can rebind ANY name to an unseen value (codex r7)
        self.loop_bound = set()     # names bound to a compiled graph INSIDE a loop/comprehension (codex r8)
        self._loop_depth = 0        # >0 while visiting a For/While/comprehension body
        self.passed_as_arg = set()  # StateGraph vars passed as a bare-Name argument → may be mutated elsewhere

    def _g(self, var):
        return self.graphs.setdefault(var, {"nodes": [], "edges": [], "unmodeled": None})

    def _ambiguous(self, name):
        # A name is safe to resolve by-name ONLY if it has EXACTLY ONE binding site and is not imported. ANY
        # second binding — a non-compile rebind (`c = factory()`), an if/else branch, a sibling scope, a
        # class body, a walrus, a tuple-unpack, a parameter — makes the runtime value non-deterministic to
        # static analysis ⇒ fail closed (audit-3 codex rounds 3-6). A `from X import *` anywhere can rebind
        # ANY name to an unseen value, so it poisons EVERY name (codex r7). A name bound to a compiled graph
        # inside a loop refers to a different instance each iteration (codex r8).
        return (self.star_import or name in self.imported or name in self.loop_bound
                or self.store_count.get(name, 0) > 1)

    def _enter_loop(self, n):
        self._loop_depth += 1
        self.generic_visit(n)
        self._loop_depth -= 1

    # A graph (re)built or mutated inside a loop/comprehension runs its add_node/StateGraph() N times, but the
    # AST shows ONE call site — counting sites would UNDERSTATE the node count (codex r8). Mark such graphs
    # unmodeled (fail closed). Comprehensions count too: `[g.add_node(x) for x in xs]` mutates in a loop.
    visit_For = _enter_loop
    visit_AsyncFor = _enter_loop
    visit_While = _enter_loop
    visit_ListComp = _enter_loop
    visit_SetComp = _enter_loop
    visit_DictComp = _enter_loop
    visit_GeneratorExp = _enter_loop

    def visit_Import(self, n):
        for a in n.names:
            self.imported.add((a.asname or a.name).split(".")[0])
        self.generic_visit(n)

    def visit_ImportFrom(self, n):
        for a in n.names:
            if a.name == "*":
                self.star_import = True   # `from X import *` — opaque rebind of any name ⇒ poison all names
            else:
                self.imported.add(a.asname or a.name)
        self.generic_visit(n)

    def _mark_unmodeled(self, recv, keywords):
        """APIs that break the ≤1-execution-per-super-step model (audit-3 codex, verified against langgraph
        graph/state.py): a RetryPolicy re-runs a node WITHIN a super-step; an error_handler is an EXTRA node
        that runs on failure AND is itself retried. Both exist per-node (add_node) and graph-wide
        (set_node_defaults). v1 cannot bound the multiplier ⇒ record WHY and fail closed in _resolve.
        A `**kwargs` spread (keyword.arg is None) is OPAQUE — it may carry retry_policy/error_handler — so
        it also fails closed (audit-3 codex)."""
        for k in keywords:
            if k.arg is None:
                self._g(recv)["unmodeled"] = ("spreads **kwargs into a node (may carry retry_policy / "
                                              "error_handler; opaque to static analysis)")
            elif k.arg in ("retry_policy", "retry"):
                self._g(recv)["unmodeled"] = ("uses a RetryPolicy (retries re-run a node within a "
                                              "super-step; not modeled in v1)")
            elif k.arg == "error_handler":
                self._g(recv)["unmodeled"] = ("uses an error_handler (an extra node that runs on failure "
                                              "and is itself retried; not modeled in v1)")

    def _record_binding(self, tgt, v):
        """Record `tgt = StateGraph()/X.compile()/Pregel()` (from an Assign OR a walrus `:=`), for resolution
        (compiled_from/pregel_vars/graphs) and the ambiguity guard. A binding inside a loop refers to a fresh
        instance each iteration (codex r8) ⇒ mark it (fail closed)."""
        cn = call_name(v).split(".")[-1]
        if cn == "StateGraph":
            self._g(tgt)
            if self._loop_depth > 0:
                self._g(tgt)["unmodeled"] = "graph (re)built inside a loop/comprehension (node count not statically bounded)"
        elif cn == "compile" and isinstance(v.func, ast.Attribute) and isinstance(v.func.value, ast.Name):
            self.compiled_from[tgt] = v.func.value.id
            if self._loop_depth > 0:
                self.loop_bound.add(tgt)
        elif cn == "Pregel":
            self.pregel_vars.add(tgt)
            if self._loop_depth > 0:
                self.loop_bound.add(tgt)

    def visit_Assign(self, n):
        if len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and isinstance(n.value, ast.Call):
            self._record_binding(n.targets[0].id, n.value)
        self.generic_visit(n)

    def visit_NamedExpr(self, n):
        # walrus binding `(c := X.compile())` — recorded so it can't slip past the ambiguity guard (audit-3).
        if isinstance(n.target, ast.Name) and isinstance(n.value, ast.Call):
            self._record_binding(n.target.id, n.value)
        self.generic_visit(n)

    @staticmethod
    def _recv(n):
        f = n.func
        return f.value.id if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) else None

    def visit_Call(self, n):
        last = call_name(n).split(".")[-1]
        recv = self._recv(n)
        # a graph var passed as a BARE-NAME argument may be mutated by the callee (`build(inner)` adds nodes
        # to `inner` via the callee's param, which we attribute to the PARAM, not `inner`) → node count
        # undercounted. Record every bare-Name arg; _resolve fails closed for a StateGraph var among them.
        # (The subgraph-alias slot passes a COMPILE RESULT alias, not a StateGraph var, so it's unaffected.)
        for a in list(n.args) + [k.value for k in n.keywords]:
            if isinstance(a, ast.Name):
                self.passed_as_arg.add(a.id)
        if last == "add_sequence" and recv is not None:
            # add_sequence([...]) adds several nodes we don't individually count → fail closed.
            self._g(recv)["unmodeled"] = "uses add_sequence (its nodes are not individually counted in v1)"
        if last == "add_node" and recv is not None:
            arg0 = n.args[0] if n.args else None
            nname = const_of(arg0) if arg0 is not None else None
            nname = nname if isinstance(nname, str) else None
            self._g(recv)["nodes"].append((nname, n.lineno))
            # an add_node inside a loop/comprehension runs N times — the AST shows ONE site, so counting
            # sites would UNDERSTATE the node count (codex r8). Fail closed.
            if self._loop_depth > 0:
                self._g(recv)["unmodeled"] = "nodes added inside a loop/comprehension (count not statically bounded)"
            # per-node RetryPolicy / error_handler re-execute beyond our ≤1-per-super-step model
            # (audit-3 BLOCKER) → fail closed in _resolve. Detection shared with set_node_defaults.
            self._mark_unmodeled(recv, n.keywords)
            for a in list(n.args[1:]) + [k.value for k in n.keywords]:
                inner, alias = None, None
                if (isinstance(a, ast.Call) and call_name(a).split(".")[-1] == "compile"
                        and isinstance(a.func, ast.Attribute) and isinstance(a.func.value, ast.Name)):
                    inner = a.func.value.id                  # inline   add_node("sub", inner.compile())
                elif isinstance(a, ast.Name) and a.id in self.compiled_from:
                    inner, alias = self.compiled_from[a.id], a.id   # aliased  compiled=inner.compile(); add_node("sub", compiled)
                elif isinstance(a, ast.Name) and a.id in self.pregel_vars:
                    inner, alias = a.id, a.id                # Pregel var: not a StateGraph ⇒ _resolve = non_certifiable
                if inner is not None:
                    # `alias` (the Name actually passed) is checked for ambiguity at resolve time: an imported
                    # or multi-scope alias may be a DIFFERENT graph at runtime than the binding we recorded.
                    self.subgraph_nodes.append((recv, nname, inner, alias, n.lineno))
        elif last == "set_node_defaults" and recv is not None:
            # graph-wide RetryPolicy / error_handler (StateGraph.set_node_defaults) — same understatement
            # as the per-node kwargs (audit-3 codex, verified vs langgraph state.py). Fail closed.
            self._mark_unmodeled(recv, n.keywords)
        elif last == "add_edge" and recv is not None:
            a = const_or_endref(n.args[0]) if len(n.args) > 0 else None
            b = const_or_endref(n.args[1]) if len(n.args) > 1 else None
            self._g(recv)["edges"].append({"kind": "static", "src": a, "dst": b, "line": n.lineno})
        elif last == "add_conditional_edges" and recv is not None:
            mp = next((x for x in list(n.args) + [k.value for k in n.keywords] if isinstance(x, ast.Dict)), None)
            if mp is not None:
                self._g(recv)["edges"].append({"kind": "conditional-literal", "src": None,
                                               "dsts": [const_of(v) for v in mp.values], "line": n.lineno})
            else:
                self._g(recv)["edges"].append({"kind": "conditional-fn", "src": None, "dsts": None, "line": n.lineno})
        elif last in ("invoke", "ainvoke", "stream", "astream"):
            # link the recursion_limit back to its StateGraph var, two call shapes:
            #   (a) app = g.compile(); app.invoke(config=...)      → recv = app, src = compiled_from[app]
            #   (b) g.compile().invoke(config=...)  (chained)       → src = the compile()'s receiver g
            src = self.compiled_from.get(recv) if recv is not None else None
            if src is None and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Call):
                ic = n.func.value
                if (call_name(ic).split(".")[-1] == "compile" and isinstance(ic.func, ast.Attribute)
                        and isinstance(ic.func.value, ast.Name)):
                    src = ic.func.value.id
            if src is not None:
                for k in n.keywords:
                    if k.arg == "config" and isinstance(k.value, ast.Dict):
                        for kk, vv in zip(k.value.keys, k.value.values):
                            if const_of(kk) == "recursion_limit":
                                self.invoke_limit[src] = const_of(vv) if const_of(vv) is not None else "unresolved"
        self.generic_visit(n)

    def to_dict(self):
        """Serializable per-graph analysis, attached to the ExtractionResult by extract_unit."""
        return {
            "graphs": {v: {"nodes": [list(t) for t in g["nodes"]], "edges": g["edges"],
                           "unmodeled": g.get("unmodeled"), "ambiguous": self._ambiguous(v),
                           "passed_as_arg": v in self.passed_as_arg}
                       for v, g in self.graphs.items()},
            "invoke_limit": dict(self.invoke_limit),
            "subgraph_nodes": [list(t) for t in self.subgraph_nodes],
            "ambiguous_names": sorted(n for n in (set(self.store_count) | self.imported) if self._ambiguous(n)),
        }


def analyze(tree) -> dict:
    """Run the per-graph receiver analysis over an already-parsed AST. Called by extract_unit ONLY when a
    `subgraph-node` feature is present (the flat path is otherwise untouched)."""
    R = _GraphReceivers()

    def bump(name):
        if name:
            R.store_count[name] = R.store_count.get(name, 0) + 1

    # Count EVERY binding site of each name so the ambiguity guard fails closed on any name bound >1× — across
    # scopes, branches, rebinds (compile or not), OR a shadowing binding the name-keyed map would miss (codex
    # rounds 3-6). This enumerates ALL of Python's name-binding forms, not just ast.Name(Store):
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Name) and isinstance(nd.ctx, ast.Store):
            bump(nd.id)                                   # assign/augassign/annassign/for/with-as/walrus/unpack
        elif isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            ar = nd.args                                  # parameters (ast.arg, NOT ast.Name)
            for p in (list(ar.posonlyargs) + list(ar.args) + list(ar.kwonlyargs)
                      + ([ar.vararg] if ar.vararg else []) + ([ar.kwarg] if ar.kwarg else [])):
                bump(p.arg)
            if not isinstance(nd, ast.Lambda):
                bump(nd.name)                             # the def name binds in the enclosing scope
        elif isinstance(nd, ast.ClassDef):
            bump(nd.name)
        elif isinstance(nd, ast.ExceptHandler):
            bump(nd.name)                                 # except E as x
        elif isinstance(nd, ast.MatchAs):
            bump(nd.name)                                 # case … as x / case x
        elif isinstance(nd, ast.MatchStar):
            bump(nd.name)                                 # case [*rest]
        elif isinstance(nd, ast.MatchMapping):
            bump(nd.rest)                                 # case {**rest}
    R.visit(tree)
    return R.to_dict()


def _nc(prov):       return {"category": "non_certifiable", "bound_factor": None, "prov": prov}
def _runaway(prov):  return {"category": "runaway", "bound_factor": None, "prov": prov}


def _resolve(var, A, seen, depth, parent_limit=0):
    """Recursively resolve a graph variable's {category, bound_factor, prov} from the analysis dict `A`.
    ABSORBING on non_certifiable/runaway (drops the number). Conservative bound_factor (never understates).
    `parent_limit` is the enclosing graph's recursion_limit: a subgraph WITHOUT its own explicit limit
    inherits the parent's config in LangGraph, so we use max(parent_limit, default) — never smaller
    (audit-3 deepseek)."""
    if depth > DEPTH_CAP:
        return _nc(f"{var}: composition depth > {DEPTH_CAP}")
    if var in seen:
        return _nc(f"{var}: subgraph definition cycle")
    if var not in A["graphs"]:
        return _nc(f"{var}: subgraph not resolvable in this file (imported / dynamic)")
    seen = seen | {var}
    g = A["graphs"][var]
    # AMBIGUITY (audit-3 codex): this graph name is bound in >1 scope or also imported ⇒ a name-keyed lookup
    # may have merged/mis-resolved it (e.g. a sibling-scope `g = small.compile()` polluting a module-level
    # name that is actually an imported 100-node subgraph). Can't bound soundly ⇒ fail closed.
    if g.get("ambiguous"):
        return _nc(f"{var}: graph name imported or bound to a compiled graph more than once "
                   f"(ambiguous — fail closed)")
    if g.get("passed_as_arg"):
        # the graph var was passed into a function call, which may add nodes we attribute to the callee's
        # parameter instead of this var → node count undercounted (codex r8 follow-on). Fail closed.
        return _nc(f"{var}: graph passed as an argument to a function (may be mutated out of view — fail closed)")
    # RetryPolicy / error_handler (per-node or graph-wide via set_node_defaults) re-execute beyond our
    # ≤1-per-super-step model ⇒ understatement. v1 fails closed with the reason (audit-3 codex, verified
    # vs langgraph state.py). The flat path's same retry gap is a documented follow-up.
    if g.get("unmodeled"):
        return _nc(f"{var}: {g['unmodeled']}")

    rl = A["invoke_limit"].get(var)
    if rl == "unresolved":
        return _nc(f"{var}: recursion_limit is a non-constant expression")
    if isinstance(rl, int):
        outer_steps, cat, prov = rl, "certifiable", f"{var}(explicit {rl})"
    else:                                                   # no explicit limit ⇒ inherits parent's, else default
        outer_steps = max(parent_limit, DEFAULTS["langgraph_recursion_limit_modern"])
        cat, prov = "default_dependent", f"{var}(default/inherited {outer_steps})"
    if cat == "certifiable" and outer_steps >= HUGE_LIMIT:
        return _runaway(f"{var}: recursion_limit {outer_steps} ≥ {HUGE_LIMIT}")

    amb = set(A.get("ambiguous_names", []))
    sub = [(nn, iv, al) for (ov, nn, iv, al, _l) in A["subgraph_nodes"] if ov == var]
    inner_sum = 0
    for (node_name, inner_var, alias) in sub:
        # the alias actually passed (e.g. `c` in add_node("sub", c)) may be imported or bound across scopes,
        # so at runtime it could be a DIFFERENT (bigger) graph than the binding we recorded ⇒ fail closed.
        if alias is not None and alias in amb:
            return _nc(f"{var}.{node_name}: subgraph passed via '{alias}', imported or bound more than "
                       f"once (ambiguous — fail closed)")
        r = _resolve(inner_var, A, seen, depth + 1, parent_limit=outer_steps)   # propagate for inheritance
        if r["category"] in ("non_certifiable", "runaway"):
            return {**r, "prov": f"{var}.{node_name} → {r['prov']}"}   # ABSORB, drop number
        if r["category"] == "default_dependent":
            cat = "default_dependent"
        inner_sum += r["bound_factor"]
        prov += f" × {node_name}[{r['prov']}]"

    # CONSERVATIVE per-super-step cost: EVERY node may run once (= its own wrapper execution, including the
    # subgraph wrapper — audit-3 codex: n_TOTAL, not n_normal), and a subgraph node ADDS its inner bound on
    # top. So cost = n_total + Σ inner_bound_factor. NO HUGE check on the PRODUCT (a large composed ceiling
    # is legitimate, sound info); runaway only for a single graph's explicit recursion_limit ≥ HUGE_LIMIT.
    n_total = len(g["nodes"])
    bound_factor = outer_steps * max(n_total + inner_sum, 1)
    return {"category": cat, "bound_factor": bound_factor, "prov": prov}


def compose(ex_flat: dict) -> dict | None:
    """Called by the mapper when the flat extractor flagged `subgraph-node`. Reads the per-graph analysis
    attached by extract_unit (`ex_flat["subgraph_analysis"]`). Returns a mapping-result dict (the shape
    map_unit returns) or None to fall back to the flat path. Refuses to compose (honest non_certifiable)
    when the no-fan-out invariant can't be held or the inner graph isn't resolvable."""
    A = ex_flat.get("subgraph_analysis")
    if not A or not A.get("subgraph_nodes"):
        return None
    base = {"unit_id": ex_flat.get("unit_id"), "kind": ex_flat.get("kind", "langgraph")}

    # NO-FAN-OUT INVARIANT (council P0 / FR-007): any Send / dynamic-goto ⇒ composition unsound.
    bad = {fe["feature"] for fe in ex_flat.get("features", [])} & _FANOUT_FEATURES
    if bad:
        return {**base, "category": "no-mapeable:subgraph-node",
                "reason": f"fan-out present ({sorted(bad)}) — composition unsound, not attempted",
                "all_blocking": sorted(bad)}

    inner_vars = {iv for (_o, _n, iv, _al, _l) in A["subgraph_nodes"]}
    outers = [v for v in A["graphs"] if v not in inner_vars]
    if len(outers) != 1:
        return {**base, "category": "no-mapeable:subgraph-node",
                "reason": f"no unique outer graph (candidates {sorted(outers)})"}

    outer = outers[0]
    res = _resolve(outer, A, seen=frozenset(), depth=0)
    if res["category"] == "non_certifiable":
        return {**base, "category": "no-mapeable:subgraph-node", "reason": res["prov"]}
    if res["category"] == "runaway":
        return {**base, "category": "rechaza-con-razon", "reason": res["prov"]}
    # the outer's OWN recursion_limit drives the displayed `supersteps`; the composed total is the
    # node-executions ceiling (aggregation=sum renders it as "≤S supersteps × N nodes = ≤total").
    ostep = A["invoke_limit"].get(outer)
    outer_steps = ostep if isinstance(ostep, int) else DEFAULTS["langgraph_recursion_limit_modern"]
    internal = "tipa:explicit" if res["category"] == "certifiable" else "tipa:framework-default"
    out = {**base, "category": internal, "supersteps": outer_steps, "bound_factor": res["bound_factor"],
           "aggregation": "sum", "composed": True, "composition": res["prov"],
           "bound_source": "explicit" if internal == "tipa:explicit" else "framework-default(composed)"}
    if internal == "tipa:framework-default":
        out["default_caveat"] = "a nested subgraph relies on a framework default ⟹ near-vacuous (D8)"
    return out
