"""Schema gasket.v1 (CONGELADO — council 002 P0-3) + salida humana.

Enums cerrados:
  category   ∈ {certifiable, default_dependent, non_certifiable, runaway, parse_error}
  provenance ∈ {explicit, framework_default, absent}
  aggregation∈ {max, sum}
Campos nuevos: solo aditivos en gasket.v2. `signature` reservado (null en OSS; capa E1 lo firma).
"""
import hashlib
import json

SCHEMA = "gasket.v1"

# mapeo taxonomía-001 → enum público v1
_CAT = {
    "tipa:explicit": "certifiable",
    "tipa:framework-default": "default_dependent",
    "rechaza-con-razon": "runaway",
    "extractor-failure": "parse_error",
}


def public_category(internal: str) -> str:
    if internal.startswith("no-mapeable:"):
        return "non_certifiable"
    return _CAT.get(internal, "parse_error")


def stable_unit_id(repo_rel_path: str, kind: str, line: int) -> str:
    return hashlib.sha256(f"{repo_rel_path}:{kind}:{line}".encode()).hexdigest()[:12]


def to_v1(mapped_units: list[dict]) -> dict:
    units = []
    counts = {"certifiable": 0, "default_dependent": 0, "non_certifiable": 0,
              "runaway": 0, "parse_error": 0}
    for r in mapped_units:
        cat = public_category(r["category"])
        counts[cat] += 1
        prov = "absent"
        src = r.get("bound_source", "")
        if src == "explicit":
            prov = "explicit"
        elif src.startswith("framework-default"):
            prov = "framework_default"
        units.append({
            "unit_id": stable_unit_id(r.get("rel_path", r.get("file", "?")),
                                      r.get("kind", "?"), r.get("line", 0)),
            "file": r.get("rel_path", r.get("file")),
            "span": {"line": r.get("line", 0)},
            "framework": r.get("kind"),
            "category": cat,
            "bound": {
                "supersteps": r.get("supersteps"),
                # techo REAL (audit-3 gpt-5.5 P0): con aggregation=sum el ceiling es
                # supersteps × nodos (node-executions); con max es supersteps. NUNCA subreportar.
                "node_executions_ceiling": r.get("bound_factor"),
                "aggregation": r.get("aggregation"),
                "provenance": prov,
            },
            "reasons": ([r["reason"]] if r.get("reason") else []) + r.get("all_blocking", []),
        })
    return {
        "schema": SCHEMA,
        "units": units,
        "summary": {
            "total": len(units), **counts,
            "vacuous_default_bounds": sum(
                1 for u in units
                if u["category"] == "default_dependent"
                and (u["bound"]["supersteps"] or 0) >= 1000),
        },
        "signature": None,   # reservado: la capa de certificación firmada (no-OSS) lo completa
    }


_BADGE = {"certifiable": "✓", "default_dependent": "▲", "non_certifiable": "✗",
          "runaway": "‼", "parse_error": "·"}


def pretty(report: dict, verbose: bool = False) -> str:
    s = report["summary"]
    out = []
    out.append(f"gasket — budget certificate check  (schema {report['schema']})")
    out.append("")
    rows = [u for u in report["units"]
            if verbose or u["category"] != "certifiable"]
    if rows:
        w = max((len(u["file"] or "?") for u in rows), default=10)
        for u in rows:
            b = u["bound"]
            if b["supersteps"] is not None:
                ceil = b.get("node_executions_ceiling")
                extra = (f" ×{ceil // b['supersteps']} nodes = ≤{ceil} node-executions"
                         if b.get("aggregation") == "sum" and ceil and b["supersteps"] else "")
                bound = f"≤{b['supersteps']} supersteps{extra} ({b['provenance']})"
            else:
                bound = ", ".join(u["reasons"]) or "—"
            out.append(f"  {_BADGE[u['category']]} {u['file']:<{w}} :{u['span']['line']:<5}"
                       f" {u['category']:<18} {bound}")
        out.append("")
    out.append(f"  {s['total']} graph units | ✓ {s['certifiable']} certifiable"
               f" | ▲ {s['default_dependent']} default-dependent"
               f" | ✗ {s['non_certifiable']} non-certifiable"
               f" | ‼ {s['runaway']} runaway | · {s['parse_error']} parse-error")
    if s["vacuous_default_bounds"]:
        out.append("")
        out.append(f"  ⚠ {s['vacuous_default_bounds']} unit(s) rely on a framework default of"
                   f" ≥1000 supersteps (LangGraph ≥1.0.6) — that budget ceiling is effectively"
                   f" vacuous. Set recursion_limit explicitly.")
    return "\n".join(out)


def dumps(report: dict) -> str:
    return json.dumps(report, indent=1, ensure_ascii=False, sort_keys=True)
