# Feature Specification: gasket fusion — el "producto cartesiano de certificados" (Exp #4)

**Feature Branch**: `003-gasket-fusion-cartesian-cert`
**Created**: 2026-06-13
**Status**: Draft (council 1 ronda = APRUEBA/GO con P0s incorporados — ver §Council gate)
**Input**: Ventana C del roadmap de experimentos de `eleata-verify` (`docs/EXPERIMENTS-ROADMAP.md`,
Exp #4 "Fusión gasket × eleata-verify"). Decisión heredada del council del roadmap: **"DEFER el
teorema de no-interferencia, GO el producto cartesiano"**. Consumir `eleata-verify` como CAJA NEGRA
(versión pineada), repo separado → cero colisión con las ventanas A/B.

## Contexto

Un "run de agente confiable" responde a DOS preguntas ortogonales del comprador:

1. **"¿Me va a reventar el presupuesto?"** → la responde `gasket` con un **certificado de COSTO**:
   estático, ahead-of-time, respaldado por el teorema de cost-soundness mecanizado en Lean 4
   (`vsteps_sound`, `#print axioms = [propext, Quot.sound]`, sin `sorry`, DOI Zenodo
   `10.5281/zenodo.20661092`). Vale sobre TODA traza del grafo analizado.
2. **"¿Puedo confiar en este output, o lo manda a revisión humana?"** → la responde `eleata-verify`
   con un **certificado de RIESGO**: `verify(claim, evidence, base, calibrator, mode)` devuelve un
   veredicto calibrado con un SLA de riesgo selectivo (SGR, Geifman–El-Yaniv 2017) ≤ α con prob ≥ 1−δ
   sobre i.i.d. del dominio de calibración, o se ABSTIENE (route-to-human). Runtime, per-output,
   acotado al dominio — NO universal.

Esta feature define el **producto cartesiano** de esos dos certificados: un único artefacto de
auditoría por run que LLEVA AMBOS, cada uno con su propio scope, **SIN componerlos en una garantía
conjunta**. El teorema de no-interferencia (budget ⊥ riesgo, estilo Hoare) que justificaría una
composición es **trabajo futuro DOCUMENTADO, NO vendido** (ver `docs/NON-INTERFERENCE.md`).

## Decisiones heredadas / de borde (no re-discutir)

- **gasket core = CERO dependencias runtime** (principio del proyecto, spec 002 FR-003). Por lo tanto
  la fusión es un **bundler de schema en stdlib puro**: toma dos `dict` (un `gasket.v1` y un
  `VerifyResult.to_dict()`) y emite un `gasket.fusion.v1`. **NUNCA importa `eleata_verify`** — solo
  conoce la FORMA del dict de `verify()` (contrato congelado, additive-only, `eleata-verify/docs/API-CONTRACT.md`).
- Solo el **demo** importa `eleata_verify` (y `numpy`). El demo NO es parte del runtime del package.
- **Conservadurismo > marketing**: ante cualquier duda de lectura, el bundle debe ser MÁS difícil de
  malinterpretar como "garantía compuesta", aunque cueste cobertura/elegancia.

## User Scenarios & Testing *(mandatory)*

### User Story 1 — `gasket fuse` emite el certificado combinado (Priority: P1)

Un equipo que ya corre `gasket check . --json > cost.json` en CI y produce un veredicto de riesgo
con `eleata-verify` (`risk.json`) corre `gasket fuse --cost cost.json --risk risk.json --run-id <id>`
y obtiene un único `gasket.fusion.v1` que lleva los dos certificados, sus dos scopes, los digests de
binding y el disclaimer de no-composición.

**Why this priority**: es el entregable central del Exp #4 — el artefacto auditable.

**Independent Test**: dado un `cost.json` y un `risk.json` fijos, `gasket fuse` produce un
`gasket.fusion.v1` que matchea un golden, con `composition.joint_guarantee == false` SIEMPRE.

**Acceptance Scenarios**:

1. **Given** un `cost.json` (gasket.v1) + un `risk.json` (VerifyResult dict) válidos, **When**
   `gasket fuse --cost ... --risk ... --run-id R`, **Then** salida `gasket.fusion.v1` con bloques
   `cost`, `risk`, `composition`, digests de binding (`report_digest` de ambos + `fusion_digest`),
   versiones, scopes temporales, y exit 0.
2. **Given** `--json`, **Then** JSON canónico (claves ordenadas) apto para CI; **Given** salida
   humana, **Then** los dos status lado a lado (`cost.status`, `risk.status`) + el disclaimer SIEMPRE
   visible — NUNCA un único veredicto verde/rojo agregado.
3. **Given** un `risk.json` malformado (falta un campo del contrato, o tipo/enum inválido), **Then**
   error claro a stderr + exit 2 (infraestructura), NUNCA un bundle corrupto silencioso.
4. **Given** un `risk.json` con campos EXTRA desconocidos (contrato additive-only de A), **Then** se
   acepta (tolerancia a campos nuevos) sin romper.

### User Story 2 — Demo e2e reproducible (Priority: P1)

Un script `examples/fusion_demo.py` corre el flujo COMPLETO offline y determinista: (a) `gasket check`
sobre un fixture de workflow LangGraph → cost cert; (b) `eleata_verify.verify()` REAL sobre una base
sintética (`CallableVerifier` de overlap léxico) + un `Calibrator` de `fit()` determinista → risk
cert; (c) `fuse()` → imprime el bundle. Muestra **happy-path** (`risk.status == answered`) Y
**abstención** (claim de bajo overlap → `risk.status == abstained`).

**Why this priority**: prueba la MECÁNICA de fusión con una llamada REAL a `verify()` (caja negra),
sin GPU/torch, corrible en CI. Rotulado `demonstration_only`.

**Acceptance Scenarios**:

1. **Given** el demo corrido en una venv con `eleata-verify` pineado + `numpy`, **Then** imprime dos
   bundles `gasket.fusion.v1` (answered + abstained), exit 0, determinista (mismo seed → misma salida).
2. **Given** la flag opt-in `--base nli --model-dir <dir>`, **Then** usa el adapter `toga_nli_verifier`
   real (torch) en vez de la base sintética — DEFER de implementación pesada, pero el hook existe y
   está documentado; el default (sintético) NUNCA requiere torch.

### Edge Cases

- `cost.json` o `risk.json` inexistente / no-JSON → exit 2 con mensaje (no crash).
- `risk.json` de un run ≠ `cost.json`: el binding (digests + mismo `run_id` provisto) lo hace
  AUDITABLE pero NO lo previene criptográficamente — el bundle DEBE declarar que los digests son
  tamper-EVIDENCE, no prueba de autoría (eso es la capa firmada E1).
- Calibrador no certificado (`sla_certified == false`) → `risk.status == uncertified`; el bundle NO
  debe leerse como "riesgo acotado".
- Workflow con `runaway` → `cost.status == runaway`; el bundle lo expone, no lo entierra.
- El bundle NUNCA incluye código fuente del usuario (solo digests + spans), apto para CI logs.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `src/gasket/fusion.py` (stdlib only) expone: `SCHEMA="gasket.fusion.v1"`,
  `cost_certificate(gasket_v1_report) -> dict`, `risk_certificate(verify_result_dict, *, verify_version,
  calibrator_digest=None, claim=None) -> dict`, `fuse(...) -> dict`, `canonical(obj) -> str`,
  `digest(obj) -> str` (sha256 de JSON canónico, prefijo `"sha256:"`), `pretty(bundle) -> str`.
  **NUNCA importa `eleata_verify`.**
- **FR-002**: `risk_certificate` VALIDA en stdlib el contrato congelado de `VerifyResult.to_dict()`:
  claves requeridas presentes, tipos correctos, enums válidos (`verdict`, `sla_mode`). Tolera claves
  EXTRA (additive-only). Falla con `ValueError` claro ante violación.
- **FR-003**: Binding anti-sustitución (council P0): el bundle incluye `cost.report_digest`,
  `risk.result_digest`, `risk.evidence_digest` (derivado de `evidence_cited`), `run.fusion_digest`
  (sobre el bundle con `fusion_digest`+`signature` nulos), y opcionales `cost.workflow_digest`,
  `risk.claim_digest`, `risk.calibrator_digest` (null si el caller no los provee). Mismo `run.run_id`
  para ambos. Disclaimer explícito: digests = tamper-evidence, NO autoría.
- **FR-004**: Versiones + scopes (council P0): `cost.gasket_version`, `risk.verify_version`;
  `cost.scope = "ahead-of-time; static; every-trace"`, `risk.scope = "per-output; population SLA on
  i.i.d. calibration domain"`. `cost.theorem` (name, mechanized, doi, scope) y `risk.guarantee`
  (name, scope) embebidos.
- **FR-005**: `cost.status` = peor categoría entre units (orden de severidad: runaway > non_certifiable
  > parse_error > default_dependent > certifiable); expone `summary` (incl. `vacuous_default_bounds`).
  `risk.status` ∈ {answered, abstained, uncertified}: `uncertified` si `!sla_certified`; `abstained`
  si `abstain`; `answered` en otro caso.
- **FR-006**: `composition` lleva `kind="cartesian-product"`, `joint_guarantee=false` (SIEMPRE),
  `disclaimer` (el texto duro), `non_interference` (puntero a FUTURE WORK). **NO** hay booleano
  agregado tipo `both_independently_certified` (council P0: eliminado por "halo effect"). **NO** se
  usan las palabras "agente seguro" ni "certificado combinado" como garantía.
- **FR-007**: CLI `gasket fuse` (stdlib): `--cost FILE --risk FILE --run-id ID
  [--gasket-version V] [--verify-version V] [--workflow PATH] [--calibrator-digest D] [--claim-file F]
  [--created-unix N] [--json]`. `--workflow PATH` digestea el/los `.py` para `workflow_digest`.
  Exit 0 = corrió; exit 2 = infra (file/JSON/validación). Sin `--fail-on` (un bundle no es una política).
- **FR-008**: `examples/fusion_demo.py` (US2): offline, determinista, base sintética por default;
  opt-in `--base nli --model-dir`. Rotulado `demonstration_only`. Pinea `eleata-verify` (instrucción
  de install con sha/branch en el header + `examples/requirements.txt`).
- **FR-009**: Tests `tests_pkg/test_fusion.py` (script-style stdlib, como `test_cli.py`): golden del
  schema, invariantes de honestidad (`joint_guarantee` siempre false; disclaimer presente; ausencia de
  cualquier booleano agregado de seguridad; ausencia de la frase "agente seguro"), digest
  tamper-evidence (cambiar el report cambia el digest), validación estricta de `risk_certificate`,
  CLI e2e `gasket fuse`.
- **FR-010**: Docs: `docs/NON-INTERFERENCE.md` (el teorema como TRABAJO FUTURO: enunciado Hoare-style,
  los DOS+ canales de interferencia, y por qué un teorema operacional NO preserva por sí solo la
  garantía estadística i.i.d.); `docs/FUSION.md` (qué es el producto cartesiano, el schema, el CLI, el
  límite de honestidad, el demo); sección "Fusion (experimental)" en README con el disclaimer.
- **FR-011**: El bundle OSS es informativo, NO firmado (`signature: null` reservado, igual que
  `gasket.v1`). La firma criptográfica del bundle es la capa paga (certd/E1), fuera de este repo.
- **FR-012**: CI: agregar `python tests_pkg/test_fusion.py` al workflow; el demo NO corre en CI (dep
  externa `eleata-verify` + numpy), pero su lógica de fusión sí se ejercita vía los tests stdlib.

### Key Entities

- **CostCertificate** (bloque `cost`): deriva del `gasket.v1` report + provenance del teorema.
- **RiskCertificate** (bloque `risk`): deriva del `VerifyResult.to_dict()` + metadata del calibrador.
- **FusionBundle** (`gasket.fusion.v1`): el producto cartesiano + `composition` (el límite de honestidad).

## Success Criteria *(mandatory)*

- **SC-001**: `fuse()` sobre fixtures fijos reproduce un golden `gasket.fusion.v1` byte-a-byte
  (JSON canónico) — determinismo total.
- **SC-002**: Invariante de honestidad verificado por test: para CUALQUIER input,
  `composition.joint_guarantee == false`, el `disclaimer` está presente y no vacío, y NO existe ningún
  campo booleano que agregue los dos certs en un "pasa/no-pasa" de seguridad.
- **SC-003**: `risk_certificate` rechaza (ValueError) un dict al que le falta un campo requerido del
  contrato o tiene un enum inválido; acepta un dict con campos extra desconocidos.
- **SC-004**: Tamper-evidence: alterar un byte del `cost.json` cambia `cost.report_digest` y
  `run.fusion_digest`.
- **SC-005**: El demo corre offline (sin torch) en <10s y emite los dos bundles (answered + abstained).
- **SC-006**: audit-3 (gate MÁXIMA) sin P0 antes de declarar la feature lista / pushear.

## Assumptions

- `eleata-verify` se pinea a `main`/`core-dev` actual (`aa411e9`, contrato base `cf4634f`); el contrato
  es additive-only (garantía de la ventana A en `API-CONTRACT.md`) → pinear a current main es seguro.
- El `workflow_digest`/`claim_digest` son OPCIONALES: si el caller no los provee, quedan `null` y el
  bundle lo declara (binding parcial honesto, no falso).
- La capa firmada (certd/E1) que firma el `gasket.fusion.v1` NO está en scope; solo se deja el hook
  `signature: null` (paridad con `gasket.v1`).

## Council gate (1 ronda, 2026-06-13, council-v2 6 voces): APRUEBA/GO — P0s INCORPORADOS

1. **Binding cruzado anti-sustitución (P0)**: digests de AMBOS inputs + `fusion_digest` + binding al
   mismo `run_id` + `workflow_digest`/`claim_digest`/`evidence_digest`. Caveat: hashes = tamper-evidence,
   NO autoría → la autenticidad real es la capa firmada. (→ FR-003)
2. **Eliminar `both_independently_certified` (P0, unánime)**: un booleano positivo agregado se consume
   como badge verde ignorando el disclaimer ("halo effect"); "independently" sugiere falsa independencia
   estadística. Dejar `cost.status` + `risk.status` lado a lado. Conservar `joint_guarantee=false` (es
   una aserción NEGATIVA de seguridad, no un badge). (→ FR-006)
3. **Validación stdlib estricta del contrato `risk.json` (P0)**: tipos/enums/required, tolerando solo
   campos extra. Resuelve el "zero-dep es ilusión / contrato no validado". (→ FR-002)
4. **Versiones de ambos + scopes temporales explícitos (P0)**: `gasket_version`, `verify_version`,
   `calibrator_digest`; cost = ahead-of-time/every-trace, risk = per-output/i.i.d.-population. (→ FR-004)
5. **Segundo+ canal de interferencia + estadística≠operacional (P0)**: el verificador consume el mismo
   budget (trunca el razonamiento → corre la distribución); gaming/feedback (el agente evita la zona de
   riesgo); retries/selection/optional-stopping/drift/tratamiento de abstenciones. Y: un teorema Hoare
   OPERACIONAL no preserva por sí solo una garantía ESTADÍSTICA (i.i.d./exchangeability) — por eso el
   teorema es difícil. Todo en `docs/NON-INTERFERENCE.md`. (→ FR-010)
6. **Demo rotulado `demonstration_only` + NLI real opt-in (P0)**: el demo sintético prueba la mecánica;
   DEFER el NLI real pesado para esta ventana, pero dejar el hook documentado. (→ FR-008)
7. **Disclaimer mínimo NON-NEGOTIABLE** (texto de codex, adoptado): "Este bundle contiene afirmaciones
   separadas y scopeadas. NO establece independencia estadística, garantía conjunta, seguridad global,
   ni preservación del SLA de riesgo ante cambios inducidos por presupuesto, prompts, modelos,
   herramientas, reintentos, selección o políticas de abstención." (→ FR-006)
