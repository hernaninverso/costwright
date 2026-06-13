# Implementation Plan: costwright fusion (003)

**Branch**: `003-costwright-fusion-cartesian-cert` | **Spec**: `./spec.md` | **Date**: 2026-06-13

## Constitution Check

costwright no tiene constitución ratificada (template vacío). Principios de facto heredados (spec 001/002),
verificados contra esta feature:

- **Library-First / CLI**: la fusión es un módulo `costwright.fusion` + un subcomando `costwright fuse`. ✓
- **Zero runtime deps (load-bearing)**: el bundler es stdlib puro; NUNCA importa `eleata_verify`. La
  única cosa que toca `eleata_verify` es el `examples/` (no-runtime). ✓ — VERIFICADO por test (FR-002).
- **Test-First / golden**: tests script-style stdlib con golden del schema + invariantes de honestidad. ✓
- **Conservadurismo > cobertura**: el bundle se diseña para ser difícil de malinterpretar (sin booleano
  agregado, disclaimer siempre visible, `joint_guarantee=false`). ✓
- **Schema versionado, additive-only**: `costwright.fusion.v1` con enums cerrados; campos nuevos solo en v2. ✓
- **No ejecuta código del usuario**: la fusión solo lee dos `dict` JSON. ✓

Sin violaciones. No requiere "Complexity Tracking".

## Arquitectura

```
costwright check . --json ───► cost.json (costwright.v1)
                                         │
eleata_verify.verify() ──► risk.json (VerifyResult.to_dict())   [solo el DEMO/CI del usuario importa eleata_verify]
                                         │
                          costwright fuse --cost --risk ──► costwright.fusion.v1
                          (src/costwright/fusion.py — STDLIB PURO, no importa eleata_verify)
```

**Frontera de honestidad** (el corazón de la feature): vive en `composition` — `joint_guarantee=false`,
`disclaimer`, `non_interference: FUTURE WORK`. Sin booleano agregado de "seguridad compuesta".

## Componentes

| Archivo | Qué | Deps |
|---|---|---|
| `src/costwright/fusion.py` | `cost_certificate`, `risk_certificate` (con validación stdlib), `fuse`, `canonical`, `digest`, `pretty`, `SCHEMA` | stdlib (hashlib, json) |
| `src/costwright/cli.py` (+) | subcomando `costwright fuse` | stdlib |
| `examples/fusion_demo.py` | e2e: costwright check + verify() real (base sintética) + fuse; opt-in `--base nli` | `eleata_verify`, `numpy` |
| `examples/requirements.txt` | pin de `eleata-verify` (git sha) + numpy | — |
| `examples/workflows/certifiable_graph.py` | fixture LangGraph con `recursion_limit` explícito | — (texto) |
| `tests_pkg/test_fusion.py` | golden + invariantes honestidad + tamper-evidence + validación + CLI e2e | stdlib |
| `docs/NON-INTERFERENCE.md` | teorema = trabajo futuro, 2+ canales, estadística≠operacional | — |
| `docs/FUSION.md` | qué es, schema, CLI, límite de honestidad | — |
| `README.md` (+) | sección "Fusion (experimental)" | — |
| `.github/workflows/ci.yml` (+) | correr `test_fusion.py` | — |

## Schema `costwright.fusion.v1` (congelado en esta spec; additive-only en v2)

```
{ schema, run:{run_id, created_unix|null, fusion_digest},
  cost:{ source:"costwright.v1", costwright_version, report_digest, workflow_digest|null,
         scope:"ahead-of-time; static; every-trace", summary, worst_category, status,
         theorem:{name, mechanized, doi, scope} },
  risk:{ source:"eleata-verify.verify", verify_version, result_digest, claim_digest|null,
         evidence_digest, calibrator_digest|null, scope:"per-output; i.i.d. population SLA",
         verdict, calibrated_confidence, abstain, abstain_reason, sla_mode, sla_alpha,
         sla_certified, score_outlier_warning, domain, evidence_cited, status,
         guarantee:{name, scope} },
  composition:{ kind:"cartesian-product", joint_guarantee:false, disclaimer, non_interference },
  signature:null }
```

- `cost.status` = peor entre units; severidad: runaway > non_certifiable > parse_error >
  default_dependent > certifiable.
- `risk.status` = uncertified (si `!sla_certified`) | abstained (si `abstain`) | answered.
- `digest(x)` = `"sha256:" + sha256(canonical(x))`; `canonical` = `json.dumps(sort_keys=True,
  ensure_ascii=False, separators=(",",":"))`. `fusion_digest` se computa con `fusion_digest`+`signature`
  nulos, luego se rellena (patrón estándar). `evidence_digest` = digest del string `evidence_cited`.

## Pin de eleata-verify (caja negra)

`examples/requirements.txt`:
```
# pinned black-box dependency (window A owns the contract; additive-only)
eleata-verify @ git+https://github.com/hernaninverso/eleata-verify.git@aa411e9
numpy
```
(repo privado → en local: `pip install -e ~/eleata-verify`; el demo documenta ambas vías.)

## Riesgos / mitigaciones

- **Lectura como garantía compuesta** → sin booleano agregado; `joint_guarantee=false`; disclaimer
  siempre en la salida humana; tests de invariante de honestidad.
- **Contrato de eleata-verify cambia** → additive-only garantizado por A; `risk_certificate` tolera
  campos extra y valida los requeridos → un cambio breaking se detecta (falta un required) en vez de
  corromper en silencio.
- **Demo parece juguete** → rotulado `demonstration_only` + hook opt-in NLI real documentado.

## Gates

1. spec-kit (este doc) ✓ · 2. council ✓ (APRUEBA, P0s en spec §Council gate) · 3. implementar
dead-code-first · 4. **audit-3** (NON-NEGOTIABLE) · 5. presentar a Hernán → "dale" antes de push.
