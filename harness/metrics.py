"""F3 — metrics: agregación, SC mecánicos (pre-registrados en spec.md), ranking de gaps."""
import json
from collections import Counter
from pathlib import Path

def compute(mapping, manifest):
    n = len(mapping)
    cat = Counter(r["category"] for r in mapping)
    cat_major = Counter(r["category"].split(":")[0] for r in mapping)

    tipa_exp = cat.get("tipa:explicit", 0)
    tipa_def = cat.get("tipa:framework-default", 0)
    rechaza = cat_major.get("rechaza-con-razon", 0)
    no_map = cat_major.get("no-mapeable", 0)
    ext_fail = cat_major.get("extractor-failure", 0)

    mapeable = tipa_exp + tipa_def + rechaza          # cat 3+4+5 (spec SC)
    pct_mapeable = 100.0 * mapeable / n

    # SC-004 value-prop runaway: (cat3 + cat5) / (units cíclicos o con driver)
    cyclic_units = [r for r in mapping if r.get("cyclic") or
                    r["category"] == "rechaza-con-razon"]
    runaway_numer = rechaza + sum(1 for r in mapping
                                  if r["category"] == "tipa:framework-default" and r.get("cyclic"))
    pct_runaway = (100.0 * runaway_numer / len(cyclic_units)) if cyclic_units else 0.0

    # SC-005 certifiability
    total_llm = sum(r["certifiability"]["llm_constructors"] for r in mapping)
    total_caps_finite = sum(r["certifiability"]["caps_finite"] for r in mapping)
    units_with_llm = [r for r in mapping if r["certifiability"]["llm_constructors"] > 0]
    units_capped = [r for r in units_with_llm
                    if r["certifiability"]["caps_finite"] >= r["certifiability"]["llm_constructors"]]
    pct_cert_units = (100.0 * len(units_capped) / len(units_with_llm)) if units_with_llm else 0.0

    # gaps ranking (US3)
    gaps = Counter()
    for r in mapping:
        if r["category"].startswith("no-mapeable:"):
            for f in r.get("all_blocking", [r["category"].split(":", 1)[1]]):
                gaps[f] += 1
        elif r["category"] == "extractor-failure":
            gaps[f"extractor:{r.get('reason','?')}"] += 1

    # cotas numéricas resultantes (D8: surfacear vacuidad del default 1000)
    bounds_dist = Counter()
    for r in mapping:
        if r["category"].startswith("tipa:"):
            ss = r.get("supersteps") or 0
            bounds_dist["≤25" if ss <= 25 else ("26-100" if ss <= 100 else
                        ("101-999" if ss < 1000 else "≥1000 (vacuo)"))] += 1

    # veredicto mecánico (FALSO-RECHAZO pendiente de F4: se evalúa en dos pasos)
    sc = {}
    sc["SC-001_go_fuerte_cobertura"] = pct_mapeable >= 60.0
    sc["SC-002_rango_condicional"] = 30.0 <= pct_mapeable < 60.0
    sc["SC-003_nogo_cobertura"] = pct_mapeable < 30.0
    sc["SC-004_runaway_valueprop"] = pct_runaway >= 20.0
    verdict_cov = ("GO-fuerte(cobertura)" if sc["SC-001_go_fuerte_cobertura"]
                   else "GO-condicional" if sc["SC-002_rango_condicional"] else "NO-GO(cobertura)")

    by_kind = {}
    for r in mapping:
        k = r["kind"]; by_kind.setdefault(k, Counter())[r["category"].split(":")[0]] += 1

    return {
        "n_units": n,
        "categories": dict(cat), "categories_major": dict(cat_major),
        "pct_mapeable": round(pct_mapeable, 1),
        "tipa_explicit": tipa_exp, "tipa_framework_default": tipa_def,
        "rechaza_con_razon": rechaza, "no_mapeable": no_map, "extractor_failure": ext_fail,
        "runaway": {"pct": round(pct_runaway, 1), "numer": runaway_numer,
                    "denom_cyclic_units": len(cyclic_units)},
        "certifiability": {"total_llm_constructors": total_llm,
                           "total_caps_finite": total_caps_finite,
                           "units_with_llm": len(units_with_llm),
                           "units_fully_capped": len(units_capped),
                           "pct_units_fully_capped": round(pct_cert_units, 1)},
        "gaps_ranking": gaps.most_common(),
        "bounds_distribution": dict(bounds_dist),
        "by_kind": {k: dict(v) for k, v in by_kind.items()},
        "sc_eval": sc, "verdict_coverage": verdict_cov,
        "verdict_final": "PENDIENTE F4 (falso-rechazo manual) — cobertura: " + verdict_cov,
    }

if __name__ == "__main__":
    ROOT = Path(__file__).resolve().parent.parent
    mapping = json.loads((ROOT / "results" / "mapping.json").read_text())
    manifest = [json.loads(l) for l in (ROOT / "dataset" / "frozen_manifest.jsonl").read_text().splitlines()]
    m = compute(mapping, manifest)
    (ROOT / "results" / "metrics.json").write_text(json.dumps(m, indent=1, ensure_ascii=False))
    print(json.dumps(m, indent=1, ensure_ascii=False))
