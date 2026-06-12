"""F2b — mapper: árbol de decisión D4 estricto sobre ExtractionResult → MappingResult.

Orden (primera que aplica):
 1 extractor-failure   2 no-mapeable:<feature>   3 rechaza-con-razon
 4 tipa:explicit       5 tipa:framework-default
Bound global D2: n·Σ(nodos) default; n·max solo cadena lineal probada. Send → no-mapeable.
Certifiability (ortogonal): % caps finitos.
"""
import json
from pathlib import Path

from extract import DEFAULTS

BLOCKING = ("send-fanout", "dynamic-goto", "hierarchical-manager", "interrupt-human-in-loop",
            "subgraph-node")  # subgraph-node: delegate() lo cubre en teoría; harness v1 no lo mapea (rev D5)
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
    linear = (not cyclic) and all(e["kind"] == "static" for e in ex["edges"])

    def bound_term(n, source):
        agg = "max" if linear else "sum"
        return {"term": f"loop({n}, if(n₁..n_{n_nodes}))", "supersteps": n,
                "aggregation": agg, "bound_factor": n if linear else n * n_nodes,
                "bound_source": source}

    # 4 — tipa:explicit
    if kind == "langgraph":
        rl = [b for b in explicit if b["param"] == "recursion_limit"]
        if rl:
            return {**base, "category": "tipa:explicit", **bound_term(rl[0]["value"], "explicit"),
                    "cyclic": cyclic}
        n = DEFAULTS["langgraph_recursion_limit_modern"]
        return {**base, "category": "tipa:framework-default",
                **bound_term(n, "framework-default(1000 moderno; 25 legacy)"),
                "cyclic": cyclic, "default_caveat": "default=1000 ⟹ certificado casi vacuo (D8)"}
    if kind == "crewai":
        mi = [b for b in explicit if b["param"] == "max_iter"]
        if mi:
            return {**base, "category": "tipa:explicit", **bound_term(mi[0]["value"], "explicit"),
                    "cyclic": cyclic}
        return {**base, "category": "tipa:framework-default",
                **bound_term(DEFAULTS["crewai_max_iter"], "framework-default(20)"), "cyclic": cyclic}
    if kind == "agents_sdk":
        mt = [b for b in explicit if b["param"] == "max_turns"]
        if mt:
            return {**base, "category": "tipa:explicit", **bound_term(mt[0]["value"], "explicit"),
                    "cyclic": cyclic}
        return {**base, "category": "tipa:framework-default",
                **bound_term(DEFAULTS["agents_sdk_max_turns"], "framework-default(10)"),
                "cyclic": cyclic}
    return {**base, "category": "extractor-failure", "reason": f"kind-desconocido:{kind}"}

def certifiability(ex: dict) -> dict:
    caps = ex.get("caps", [])
    finite = [c for c in caps if isinstance(c.get("value"), int) and c["value"] > 0]
    return {"llm_constructors": ex.get("llm_constructors", 0), "caps_found": len(caps),
            "caps_finite": len(finite)}

if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parent.parent
    man = {m["unit_id"]: m for m in
           (json.loads(l) for l in (ROOT / "dataset" / "frozen_manifest.jsonl").read_text().splitlines())}
    exs = json.loads((ROOT / "results" / "extraction.json").read_text())
    out = []
    for ex in exs:
        r = map_unit(ex, man[ex["unit_id"]])
        r["certifiability"] = certifiability(ex)
        out.append(r)
    (ROOT / "results" / "mapping.json").write_text(json.dumps(out, indent=1, ensure_ascii=False))
    from collections import Counter
    print(Counter(r["category"].split(":")[0] for r in out))
