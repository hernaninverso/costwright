"""F2b — mapper: árbol de decisión D4 estricto sobre ExtractionResult → MappingResult.

Orden (primera que aplica):
 1 extractor-failure   2 no-mapeable:<feature>   3 rechaza-con-razon
 4 tipa:explicit       5 tipa:framework-default
Bound global D2: n·Σ(nodos) default; n·max solo cadena lineal probada. Send → no-mapeable.
Certifiability (ortogonal): % caps finitos.
"""

from costwright.extract import DEFAULTS

# subgraph-node REMOVED from BLOCKING (feature 005): composed by costwright.subgraph.compose() under the
# no-fan-out invariant. send-fanout/dynamic-goto stay BLOCKING — they ARE the fan-out vectors, so a
# subgraph graph that also has them is non_certifiable at step 2 BEFORE composition (soundness guard).
BLOCKING = ("send-fanout", "dynamic-goto", "hierarchical-manager", "interrupt-human-in-loop",
            "addnode-escaped", "add-sequence-dynamic", "construct-escaped", "node-unmodeled-retry",
            "node-in-loop", "node-helper-multicall")
HUGE_LIMIT = 10_000  # recursion_limit explícito ≥ esto = "efectivamente no acotado" (rechazo)

def map_unit(ex: dict, meta: dict) -> dict:
    uid = ex["unit_id"]
    base = {"unit_id": uid, "kind": ex.get("kind", meta.get("kind"))}

    # 1 — extractor-failure
    if ex["status"] != "ok":
        return {**base, "category": "extractor-failure", "reason": ex.get("reason", "?")}
    if ex["n_nodes_dynamic"] > 0 and ex["n_nodes_named"] == 0 and ex["kind"] == "langgraph":
        return {**base, "category": "extractor-failure", "reason": "all-nodes-dynamic"}

    # 2 — no-mapeable:<feature> (gaps reales del cálculo)
    feats = sorted({f["feature"] for f in ex["features"]})
    blocking = [f for f in feats if f in BLOCKING]
    if blocking:
        return {**base, "category": f"no-mapeable:{blocking[0]}", "all_blocking": blocking}

    # 2b — feature 005: subgraph composition (only fires when a subgraph-node is present; no fan-out
    # vectors got here since they're still BLOCKING above). compose() returns a mapping result or None.
    if "subgraph-node" in feats:
        from costwright.subgraph import compose
        composed = compose(ex)
        if composed is not None:
            return composed

    # bounds según el kind (D2/D8)
    kind = ex["kind"]
    explicit_all = [b for b in ex["bounds"] if b["source"] == "explicit"]
    # max_turns=None LITERAL es una DESACTIVACIÓN explícita (D8); una expresión no-constante
    # NO lo es (rev D5: u087) — esa cae a unresolved-bound abajo
    if kind == "agents_sdk" and any(b["param"] == "max_turns" and b.get("none_literal")
                                    for b in explicit_all):
        return {**base, "category": "rechaza-con-razon", "reason": "max-turns-none"}
    # bound explícito pero no-constante (variable/expresión): la cota real es irrecuperable
    # estáticamente → extractor-limit (conservador, medido — no cae al default en silencio)
    unresolved = [b for b in explicit_all if b["value"] is None]
    if unresolved:
        return {**base, "category": "extractor-failure", "reason": "unresolved-bound",
                "params": sorted({b["param"] for b in unresolved})}
    explicit = [b for b in explicit_all if b["value"] is not None]
    # an explicit bound < 1 (recursion_limit / max_iter / max_turns ≤ 0) is not a valid run budget — the
    # framework rejects it at runtime — and would yield a zero/NEGATIVE ceiling, which is nonsensical and
    # understates any real run. Fail closed (codex/Cursor r69; mirrors the subgraph v≥1 guard).
    nonpositive = [b for b in explicit if isinstance(b["value"], int) and not isinstance(b["value"], bool)
                   and b["value"] < 1]
    if nonpositive:
        return {**base, "category": "extractor-failure", "reason": "nonpositive-bound",
                "params": sorted({b["param"] for b in nonpositive})}
    cyclic = ex["has_static_cycle"] or ex["cond_may_cycle"] or bool(ex["while_true_invokes"])

    # 3 — rechaza-con-razon: genuinamente no acotado
    if ex["while_true_invokes"] and not any(b["param"] == "max_turns" for b in explicit):
        return {**base, "category": "rechaza-con-razon", "reason": "while-true-driver",
                "lines": ex["while_true_invokes"]}
    huge = [b for b in explicit if b["param"] == "recursion_limit" and b["value"] and b["value"] >= HUGE_LIMIT]
    if huge:
        return {**base, "category": "rechaza-con-razon", "reason": "recursion-limit-huge",
                "value": huge[0]["value"]}

    # término del cálculo + cota (D2: presupuesto global n·Σ; lineal probada → n·max)
    n_nodes = max(ex["n_nodes"], 1)
    # the `linear` (1 node per super-step ⇒ bound = supersteps) optimization is sound ONLY for a true CHAIN.
    # If any source (incl. START) has ≥2 distinct static successors, the graph FANS OUT — multiple nodes run in
    # one super-step (e.g. START→n0..n_k all activate in super-step 1), so the bound must be supersteps × n_nodes
    # (codex r65). Conditional edges already make it non-linear (not all-static).
    _dsts = {}
    for e in ex["edges"]:
        if e["kind"] == "static" and e.get("src") is not None and e.get("dst") is not None:
            _dsts.setdefault(e["src"], set()).add(e["dst"])
    static_fanout = any(len(d) >= 2 for d in _dsts.values())
    linear = (not cyclic) and all(e["kind"] == "static" for e in ex["edges"]) and not static_fanout

    def bound_term(n, source):
        agg = "max" if linear else "sum"
        return {"term": f"loop({n}, if(n₁..n_{n_nodes}))", "supersteps": n,
                "aggregation": agg, "bound_factor": n if linear else n * n_nodes,
                "bound_source": source}

    # 4 — tipa:explicit. MULTIPLE explicit bounds of the same param must COMBINE, not take the first (codex r70,
    # which printed the FIRST max_iter and understated). LangGraph invokes are SEPARATE runs ⇒ the per-run
    # worst case is the MAX recursion_limit (consistent with the subgraph path's _merge_invoke_limit). CrewAI
    # agents / Agents-SDK handoffs run SEQUENTIALLY within one execution ⇒ the worst case is the SUM of their
    # iteration budgets (conservative; a single agent contributes its own max_iter).
    if kind == "langgraph":
        rl = [b for b in explicit if b["param"] == "recursion_limit"]
        if rl:
            return {**base, "category": "tipa:explicit",
                    **bound_term(max(b["value"] for b in rl), "explicit"), "cyclic": cyclic}
        n = DEFAULTS["langgraph_recursion_limit_modern"]
        return {**base, "category": "tipa:framework-default",
                **bound_term(n, "framework-default(1000 moderno; 25 legacy)"),
                "cyclic": cyclic, "default_caveat": "default=1000 ⟹ certificado casi vacuo (D8)"}
    if kind == "crewai":
        # per-run worst case for a SEQUENTIAL crew = n_tasks × max(agent iteration budget). Each of the n_tasks
        # tasks runs ITS agent up to that agent's max_iter ⇒ ≤ n_tasks × max_budget activations (codex r86 +
        # r86b). The OLD model summed only the EXPLICIT agent budgets, which (a) ignored n_tasks entirely
        # (a single agent reused across k tasks understated k×) and (b) omitted the default 20 of unannotated
        # agents in a mixed crew. Both are fixed here; everything not statically pinned fails closed.
        crews = ex.get("crewai_crews") or []
        budgets = ex.get("crewai_agent_budgets") or []
        if not crews:
            return {**base, "category": "extractor-failure", "reason": "crewai-no-crew"}
        # each Crew() is a separate kickoff = a separate run ⇒ per-run worst case is the MAX over crews. A task
        # count that is absent (None) or dynamic ("dynamic") leaves the run length unpinned → fail closed.
        if any(not isinstance(c["tasks"], int) for c in crews):
            return {**base, "category": "extractor-failure", "reason": "crewai-tasks-unpinned"}
        n_tasks = max(c["tasks"] for c in crews)
        if n_tasks < 1:
            return {**base, "category": "extractor-failure", "reason": "crewai-empty-tasks"}
        if not budgets:
            return {**base, "category": "extractor-failure", "reason": "crewai-no-visible-agents"}
        # if a crew references MORE agents than the visible Agent() constructors, or its agents= is dynamic, at
        # least one agent is imported/unbounded — its max_iter could exceed any visible budget → fail closed.
        if any(c["agents"] == "dynamic" or (isinstance(c["agents"], int) and c["agents"] > len(budgets))
               for c in crews):
            return {**base, "category": "extractor-failure", "reason": "crewai-agents-not-visible"}
        # a None budget = a spread / non-constant max_iter (already caught by the unresolved-bound check above);
        # defensive re-check so a future path cannot certify an unrecoverable budget.
        if any(b is None for b in budgets):
            return {**base, "category": "extractor-failure", "reason": "unresolved-bound", "params": ["max_iter"]}
        has_default = any(b == "default" for b in budgets)
        ceil = max(DEFAULTS["crewai_max_iter"] if b == "default" else b for b in budgets)
        n = n_tasks * ceil
        cat = "tipa:framework-default" if has_default else "tipa:explicit"
        src = f"n_tasks({n_tasks})×max_iter(default {DEFAULTS['crewai_max_iter']})" if has_default \
            else f"n_tasks({n_tasks})×max_iter({ceil})"
        return {**base, "category": cat, **bound_term(n, src), "cyclic": cyclic}
    if kind == "agents_sdk":
        mt = [b for b in explicit if b["param"] == "max_turns"]
        if mt:
            return {**base, "category": "tipa:explicit",
                    **bound_term(sum(b["value"] for b in mt), "explicit"), "cyclic": cyclic}
        return {**base, "category": "tipa:framework-default",
                **bound_term(DEFAULTS["agents_sdk_max_turns"], "framework-default(10)"),
                "cyclic": cyclic}
    return {**base, "category": "extractor-failure", "reason": f"kind-desconocido:{kind}"}

def certifiability(ex: dict) -> dict:
    caps = ex.get("caps", [])
    finite = [c for c in caps if isinstance(c.get("value"), int) and c["value"] > 0]
    return {"llm_constructors": ex.get("llm_constructors", 0), "caps_found": len(caps),
            "caps_finite": len(finite)}
