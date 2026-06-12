"""Orquestador F3: extract → map → metrics → report.md + review pack. Determinista (FR-007)."""
import json, random, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "harness"))
from extract import extract_unit
from mapper import map_unit, certifiability
from metrics import compute

SEED = 20260612

def main():
    man = [json.loads(l) for l in (ROOT / "dataset" / "frozen_manifest.jsonl").read_text().splitlines()]
    res_dir = ROOT / "results"; res_dir.mkdir(exist_ok=True)

    extraction, mapping = [], []
    for m in man:
        ex = extract_unit(ROOT / "dataset" / "frozen" / m["unit_id"], m)
        extraction.append(ex)
        r = map_unit(ex, m); r["certifiability"] = certifiability(ex)
        r["repo"] = m["repo"]; r["rel_path"] = m["rel_path"]; r["stars"] = m["stars"]
        mapping.append(r)

    metrics = compute(mapping, man)

    (res_dir / "extraction.json").write_text(json.dumps(extraction, indent=1, ensure_ascii=False, sort_keys=True))
    (res_dir / "results.json").write_text(json.dumps(
        {"seed": SEED, "mapping": mapping, "metrics": metrics}, indent=1, ensure_ascii=False, sort_keys=True))

    # review pack (D5): TODOS los cat 1/2/3 + 10 tipa al azar (seed fija)
    rev = res_dir / "review"; rev.mkdir(exist_ok=True)
    for f in rev.glob("*.md"): f.unlink()
    rejects = [r for r in mapping if not r["category"].startswith("tipa:")]
    accepted = [r for r in mapping if r["category"].startswith("tipa:")]
    rng = random.Random(SEED)
    sample_acc = rng.sample(accepted, min(10, len(accepted)))
    man_by_id = {m["unit_id"]: m for m in man}
    for r in rejects + sample_acc:
        m = man_by_id[r["unit_id"]]
        src_file = ROOT / "dataset" / "frozen" / r["unit_id"] / m["file"]
        src = src_file.read_text(encoding="utf-8", errors="ignore")
        lines = src.splitlines()
        excerpt = "\n".join(lines[:120])
        (rev / f"{r['unit_id']}_{r['category'].replace(':','-')}.md").write_text(
f"""# {r['unit_id']} — {r['category']}
repo: {m['repo']} (★{m['stars']}) · {m['rel_path']} · scope {m['scope']} (línea {m['line']})
## Mapeo
```json
{json.dumps({k: v for k, v in r.items() if k not in ('repo','rel_path','stars')}, indent=1, ensure_ascii=False)}
```
## Código (primeras 120 líneas)
```python
{excerpt}
```
## Revisión manual: [ ] correcto  [ ] falso-rechazo  [ ] falso-aceptado — notas:
""")

    # report.md
    g = metrics
    report = f"""# Experimento de cobertura — resultados (seed {SEED})

## Dataset
- {g['n_units']} graph units únicos (post dedup estructural) de 45 repos públicos filtrados (D6).
- Por framework: {json.dumps(g['by_kind'])}

## Distribución (taxónomo D4)
| Categoría | n | % |
|---|---|---|
""" + "\n".join(f"| {k} | {v} | {round(100*v/g['n_units'],1)}% |"
                for k, v in sorted(g["categories"].items(), key=lambda x: -x[1])) + f"""

## Métricas pre-registradas
- **Mapeable (cat 3+4+5): {g['pct_mapeable']}%** → SC-001 cobertura: {'PASS (GO fuerte)' if g['sc_eval']['SC-001_go_fuerte_cobertura'] else 'ver SC-002/003'}
- **Runaway value-prop (SC-004): {g['runaway']['pct']}%** de los {g['runaway']['denom_cyclic_units']} units cíclicos dependen de un default o carecen de bound → {'PASS ≥20%' if g['sc_eval']['SC-004_runaway_valueprop'] else 'FAIL <20%'}
- **Certifiability (SC-005): {g['certifiability']['pct_units_fully_capped']}%** de units con LLM tienen TODOS los calls con cap finito ({g['certifiability']['total_caps_finite']}/{g['certifiability']['total_llm_constructors']} constructores con cap).
- **Hallazgo D8**: {g['bounds_distribution'].get('≥1000 (vacuo)',0)} units "tipan" con cota ≥1000 supersteps (default moderno de LangGraph) — certificado numéricamente vacuo.

## Gaps ranking (US3 — roadmap empírico del cálculo)
""" + "\n".join(f"- {f}: {c}" for f, c in g["gaps_ranking"]) + f"""

## Veredicto
- Cobertura: **{g['verdict_coverage']}**
- Final: {g['verdict_final']}
- Falso-rechazo/aceptado: completar en `review/` (D5: {len(rejects)} rechazos/no-mapeables + {len(sample_acc)} tipa muestreados).
"""
    (res_dir / "report.md").write_text(report)
    print(f"OK: results.json + report.md + review/ ({len(rejects)+len(sample_acc)} archivos)")
    print(f"VEREDICTO (cobertura): {g['verdict_coverage']}  · mapeable {g['pct_mapeable']}%  · runaway {g['runaway']['pct']}%  · cert {g['certifiability']['pct_units_fully_capped']}%")

if __name__ == "__main__":
    main()
