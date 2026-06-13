# Tasks: gasket fusion (003)

> Orden dead-code-first: schema+bundler → CLI → demo → tests → docs → audit-3.

## Fase 1 — Bundler (stdlib, zero-dep)
- [ ] T1.1 `src/gasket/fusion.py`: `SCHEMA`, `canonical`, `digest`.
- [ ] T1.2 `cost_certificate(gasket_v1_report)`: deriva `worst_category`/`status`, `report_digest`,
      embeb `theorem` + `scope`.
- [ ] T1.3 `risk_certificate(verify_dict, *, verify_version, calibrator_digest, claim)`: **validación
      stdlib estricta** (required keys, tipos, enums `verdict`/`sla_mode`; tolera extras), deriva
      `status`, `result_digest`, `evidence_digest`, `claim_digest`, embeb `guarantee` + `scope`.
- [ ] T1.4 `fuse(...)`: arma el bundle, computa `fusion_digest`, agrega `composition` (joint_guarantee
      false + disclaimer + non_interference). SIN booleano agregado.
- [ ] T1.5 `pretty(bundle)`: salida humana = dos status lado a lado + disclaimer SIEMPRE.

## Fase 2 — CLI
- [ ] T2.1 `gasket fuse` en `cli.py`: flags (FR-007), lee/valida JSON, `--workflow` → workflow_digest,
      exit 0/2.

## Fase 3 — Demo e2e
- [ ] T3.1 `examples/workflows/certifiable_graph.py` (fixture LangGraph con recursion_limit).
- [ ] T3.2 `examples/fusion_demo.py`: gasket check (vía API interna) + base sintética (overlap) +
      `fit()` determinista + `verify()` real + `fuse()`; happy-path + abstención; opt-in `--base nli`.
- [ ] T3.3 `examples/requirements.txt` (pin eleata-verify) + header con instrucciones de install.

## Fase 4 — Tests
- [ ] T4.1 `tests_pkg/test_fusion.py` (script-style): golden schema, tamper-evidence, validación
      estricta (rechaza malformado / acepta extras), invariantes de honestidad (joint_guarantee false,
      disclaimer presente, sin booleano agregado, sin "agente seguro"), `cost.status`/`risk.status`
      derivation, CLI e2e `gasket fuse`.

## Fase 5 — Docs
- [ ] T5.1 `docs/NON-INTERFERENCE.md` (teorema futuro: Hoare-style, 2+ canales, estadística≠operacional).
- [ ] T5.2 `docs/FUSION.md` (qué es, schema, CLI, honestidad, demo).
- [ ] T5.3 README sección "Fusion (experimental)".
- [ ] T5.4 `.github/workflows/ci.yml` += `test_fusion.py`.

## Fase 6 — Gate
- [ ] T6.1 Correr `test_fusion.py` + `test_cli.py` + dogfood `gasket check src --fail-on reject` verdes.
- [ ] T6.2 Correr el demo en venv real (eleata-verify pineado + numpy) — verificar e2e vivo.
- [ ] T6.3 **audit-3** (codex + 2 frontera) sobre el diff. Incorporar P0s.
- [ ] T6.4 Presentar a Hernán. NO pushear sin "dale".
