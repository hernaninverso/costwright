# Feature Specification: certified agent run — the `interference` block (E3/P3)

**Feature Branch**: `004-certified-agent-run`
**Created**: 2026-06-13
**Status**: Draft (awaiting council review — §Council gate)
**Input**: Ventana C del roadmap de `eleata-verify` (`docs/EXPERIMENTS-ROADMAP.md`, Exp #4), **Ronda 2**:
- **E3** — álgebra de certificados / ε-interferencia. La cota de interferencia rigurosa ya está
  probada y atestiguada (`docs/non-interference/THEOREM.md`, witnesses `examples/noninterference/*`,
  estimador `eleata_verify.epsilon` con 19 tests + council + audit-3). **Esta feature CIERRA E3
  conectándolo al producto**, no re-deriva el teorema.
- **P3** — *certified agent runs*: un run de agente que lleva prueba de **costo-acotado** (costwright/Lean)
  Y salida con **riesgo-acotado** (eleata-verify), con el **ε de E3 contabilizando la interferencia**.

## Contexto

`costwright.fusion.v1` hoy empaqueta DOS certificados como **producto cartesiano** (`joint_guarantee:
false`): un costo (estático, toda traza, Lean) y un riesgo (SGR per-output, SLA poblacional acotado al
dominio). Deliberadamente **no dice nada conjunto** — porque un cap de presupuesto puede correr la
distribución de outputs fuera del dominio de calibración (canal 1), invalidando el SLA de riesgo.

E3 cerró ese hueco **cuantitativamente**: bajo supuestos (A)(C)(D), si la *masa cap-binding*
`ε = P_{o~A}(spend(o) > b)` está acotada con confianza `1−η`, entonces el riesgo desplegado degrada de
`α` a `min(1, α + ε(1+α)/(c−ε))` con confianza conjunta `1−δ−η` (union bound, sin independencia). Esto
**acota el canal 1** — no cierra los canales 2 (budget compartido), 3 (gaming del verificador),
4 (retry-on-abstain / selección / optional-stopping / drift).

Esta feature hace que un bundle pueda **llevar ese accounting** como un bloque `interference` opcional,
sin convertirse en una "garantía limpia". Es la diferencia entre *"acá hay dos hechos sueltos"* (lo que
hace hoy) y *"acá hay dos hechos + una cota condicional de cuánto el cap puede degradar el de riesgo,
con todo lo que esa cota NO cubre escrito al lado"*.

## Meta-lección (NON-NEGOTIABLE para esta feature)

Las 3 ventanas corrigieron su propio over-claim (A la compresión, B el 1%, C el ε). **Refutar nuestras
propias afirmaciones = el value prop.** Ahora ε está acotado — pero la cota es **single-channel +
condicional a supuestos atestiguados + posiblemente vacua**. El bloque `interference` debe GRITAR eso.
No se vende como garantía compuesta. Si esta feature hace que alguien lea el bundle como "ahora es
seguro", **falló**, aunque la aritmética sea correcta.

## User scenarios

### US1 — productor de un certified run (no-vacuo)
Como operador que despliega un agente con un cap de presupuesto, quiero un artefacto único que pruebe
(costo) que el agente nunca excede `b`, (riesgo) que su output va con un SLA de riesgo selectivo, y
(interferencia) **cuánto** ese cap puede degradar el SLA — para poder decir, con número y confianza,
"bajo (A)(C)(D) el riesgo desplegado ≤ α_effective". Cuando el cap es casi no-binding (medí `ε` chico
con muchas corridas sin-cap, `k=0`), `α_effective ≈ α` y `non_vacuous=true`.

### US2 — el caso vacuo (el más importante para la honestidad)
Como el mismo operador con un cap **binding** (el agente trunca seguido), corro la estimación de ε y el
bundle me dice `non_vacuous=false`, `alpha_effective=1.0`: **el cap degrada el SLA a nada**. El bundle
NO me deja fingir que el run es seguro; me dice explícitamente "recalibrá sobre el agente capeado
(B′) o relajá el cap". Este escenario debe ser un *first-class output del demo*, no una nota al pie.

### US3 — lector/auditor del bundle
Como quien recibe el bundle, leo `composition.joint_guarantee` (sigue `false`) y el bloque
`interference` con: qué supuestos atestiguó el productor, qué canales quedan ABIERTOS, la confianza
conjunta, y si la cota es vacua. Nunca leo un booleano verde "ambos pasaron".

## Modelo de datos — `conditional_analyses.channel1_budget_cap_risk` (costwright.fusion.v1, ADITIVO)

Se agrega una key **opcional** top-level `conditional_analyses` al bundle `costwright.fusion.v1` (SIBLING de
`composition`, NO adentro — P0-6). Si el caller no la provee → `conditional_analyses: null` (bundle
cartesiano puro, idéntico al de hoy — backward-compatible; `composition` INTACTO). Cuando se provee,
`fusion` la valida **por SHAPE** + **RE-CHEQUEA la aritmética** en pure-stdlib (NUNCA importa
`eleata_verify`/`numpy`). El caller computa los números con
`eleata_verify.epsilon.interference_risk_bound(...)` (caja negra), atestigua los supuestos, y pasa el
dict a `fuse(conditional_analyses=...)`.

```jsonc
"conditional_analyses": {
  "channel1_budget_cap_risk": {
    "kind": "tv-coupling-bound",                  // método (cota (ii) del teorema)
    "channel_covered": "budget-cap-distribution-shift (channel 1 of N; N unknown)",
    "source_estimator": "eleata-verify.epsilon.interference_risk_bound",
    "verify_version": "0.1.0",
    "note": "NOT a guarantee. A REPORTED, single-channel, conditional, possibly-vacuous UPPER BOUND on "
            "selective risk under CALLER-SELF-ASSERTED operational assumptions. The signed bundle BINDS "
            "it (tamper-evidence) and RE-CHECKS its arithmetic; it does NOT verify the assumptions. See "
            "open_channels (non-exhaustive) + assumption_assurance.",

    "status": "conditionally_quantified" | "vacuous" | "inapplicable",   // DERIVADO por fusion; NUNCA 'bounded'

    // --- el número, DEGRADADO semánticamente + provenance (P0-7) ---
    "channel1_conditional_risk_upper": 0.0625,    // = min(1, α+ε(1+α)/(c−ε)); fusion lo RECOMPUTA y exige match
    "conditional_bound_confidence": 0.90,         // 1−δ−η (union bound; sin independencia) — era joint_confidence
    "alpha_base": 0.05,                           // α del SLA (debe == risk.sla_alpha; cross-check)
    "eps_upper": 0.0102, "eps_hat": 0.0, "coverage_used": 0.80,
    "cap": 5.0, "spend_unit": "unit", "m": 600, "k": 0,
    "delta": 0.05, "delta_eps": 0.05,
    "bound_verification": "formula_rechecked; eps_upper_verified_k0"      // o "...; eps_upper_reported" (k>0)
                          // fusion recomputa el número; k=0 ⇒ verifica eps_upper==1−δ_eps^(1/m); k>0 ⇒ eps_upper caller-reported

    // --- la honestidad, hard-baked (P0-2) ---
    "assumptions_attested": ["A", "C", "D"],      // QUÉ supuestos OPERACIONALES declaró el caller (no medibles)
    "assumptions_complete": true,                 // {A,C,D} ⊆ attested ; si no ⇒ status=inapplicable (forzado)
    "assumption_assurance": "self_asserted",      // self_asserted (default) | evidence_attached | independently_reviewed
    "assumption_evidence_ref": null,              // URI/hash si evidence_attached/independently_reviewed (informativo)

    "open_channels": [                            // canales NO cubiertos — SIEMPRE presentes y no vacíos
      "shared-budget: verifier draws from the agent's budget (channel 2)",
      "verifier-gaming: agent optimizes to be scored low-risk while spending less (channel 3)",
      "retry-on-abstain / best-of-n / optional-stopping / selection (channel 4)",
      "policy-awareness / endogenous drift: agent changes policy because it knows the budget shrinks (channel 5)",
      "concept drift over time; adaptive/binding caps; cross-run composition; enforcement bypass; "
      "shared state/randomness; downstream handling of abstentions; unknown/unmodeled channels"
    ],
    "open_channels_non_exhaustive": true,         // P0-5: la lista NO es exhaustiva
    "warnings": [ ... ],
    "disclaimer": "..."                           // disclaimer del estimador (canales abiertos + supuestos)
  }
}
```

### Máquina de estados de `status` (DERIVADA por fusion, conservadora — nunca confía en el caller)
- **`inapplicable`** — `assumptions_complete=false` (faltó (A) y/o (C) y/o (D)). La cota (ii) **no
  aplica**: `channel1_conditional_risk_upper` NO es una cota válida (informativo). El bundle lo marca.
- **`vacuous`** — supuestos completos pero `eps_upper ≥ coverage` **o** el número saturó a `1.0`. El cap
  degrada el SLA a nada → el run NO lleva riesgo acotado bajo el cap. Prescripción: recalibrar sobre el
  agente capeado (B′) o relajar el cap / juntar más corridas sin-cap.
- **`conditionally_quantified`** — supuestos completos Y no-vacuo. *Bajo (A)(C)(D) atestiguados (con
  `assumption_assurance`) y la medición de ε*, el riesgo selectivo desplegado ≤
  `channel1_conditional_risk_upper` con confianza ≥ `conditional_bound_confidence`, **respecto del canal
  1 únicamente**; los `open_channels` (no exhaustivos) siguen sin verificar. **NO es una garantía** — es
  una cuantificación condicional de UN canal. El status más alto posible con `assurance=self_asserted`
  sigue siendo este (NUNCA promueve a algo que lea como garantía — P0-2).

### Invariantes de honestidad (cada uno = un test)
1. **`composition.joint_guarantee` SIEMPRE `false`** y `composition` INTACTO, exista o no
   `conditional_analyses`. El análisis vive afuera de `composition` (P0-6).
2. **Ningún status lee como aprobación.** No existe `bounded`; no hay booleano "ambos pasaron"; el número
   es `channel1_conditional_risk_upper` (puede ser 1.0 = inútil), nunca un verde binario. `pretty()`
   empieza con `NO JOINT GUARANTEE` y nunca colapsa a "PASS" (P0-1, P0-4).
3. **`open_channels` no vacío + `open_channels_non_exhaustive: true`** cuando hay análisis — estructural
   (P0-5).
4. **`channel1_conditional_risk_upper` solo es cota válida si `status == "conditionally_quantified"`.** En
   `vacuous`/`inapplicable`, `pretty()` no muestra un número tranquilizador sin el caveat.
5. **fusion RE-CHEQUEA la aritmética (P0-3):** recomputa `channel1_conditional_risk_upper` de
   `(alpha_base, eps_upper, coverage_used)` en pure-stdlib y exige match (tol); para k=0 verifica
   `eps_upper == 1−delta_eps^(1/m)`; setea `bound_verification`. Mismatch ⇒ `ValueError`.
6. **Cross-checks:** `alpha_base == risk.sla_alpha`; `eps_hat == k/m`; `k ≤ m`; `assumptions_attested ⊆
   {A,C,D}`; rangos `[0,1]`. Mismatch ⇒ `ValueError` (no se corrompe el bundle).
7. **`assumption_assurance` separa QUÉ de CÓN-CUÁNTA-EVIDENCIA (P0-2):** default `self_asserted`; un
   status nunca se "promueve a garantía" por self-attestation. El bloque viaja con su `disclaimer`.

## Functional requirements

- **FR-001** `fuse(...)` acepta un kwarg opcional `conditional_analyses: dict | None = None`. `None` ⇒ el
  bundle lleva `"conditional_analyses": null` (comportamiento de hoy salvo la key nueva; `composition`
  intacto).
- **FR-002** Si se provee, `fusion` valida el sub-dict `channel1_budget_cap_risk` **por shape** con
  stdlib: keys requeridas presentes, tipos/rangos (`0≤α≤1`, `0≤eps≤1`, `0<conf<1`, `m≥0`, `0≤k≤m`),
  enums cerrados (`kind`, `assumption_assurance`, `assumptions_attested ⊆ {A,C,D}`), `open_channels` no
  vacío. Extras tolerados (aditivo). Inválido ⇒ `ValueError` con la key ofensora.
- **FR-003 (P0-3) re-chequeo aritmético pure-stdlib.** `fusion` recomputa
  `channel1_conditional_risk_upper = min(1, α+ε(1+α)/(c−ε))` de `(alpha_base, eps_upper, coverage_used)`
  (saturando a 1.0 si `eps_upper ≥ coverage_used`) y exige match con el valor del caller (tol 1e-9);
  para `k=0` verifica `eps_upper == 1−delta_eps^(1/m)` y setea `bound_verification` =
  `"...eps_upper_verified_k0"`, si no `"...eps_upper_reported"` (sanity `k/m ≤ eps_upper`). Mismatch ⇒
  `ValueError`.
- **FR-004 (P0-2) status derivado + assurance.** `fusion` DERIVA `assumptions_complete = ({A,C,D} ⊆
  attested)` y el `status`: `inapplicable` si incompleto; `vacuous` si `eps_upper ≥ coverage_used` o el
  número recomputado `== 1.0`; si no `conditionally_quantified`. **Ignora cualquier `status` del caller**
  (anti over-claim). `assumption_assurance` se conserva (default `self_asserted`) pero NUNCA promueve el
  status a algo que lea como garantía.
- **FR-005** `fusion` cross-checkea `channel1.alpha_base == risk.sla_alpha`, `eps_hat == k/m` (tol),
  `k ≤ m`; mismatch ⇒ `ValueError`.
- **FR-006** `fuse` recomputa `run.fusion_digest` sobre el bundle COMPLETO (con `conditional_analyses`),
  reproducible con `fusion_digest`/`signature` null. El bloque es tamper-EVIDENT.
- **FR-007 (P0-4)** `pretty(bundle)` empieza con `⚠ NO JOINT GUARANTEE`; si hay `conditional_analyses`,
  muestra primero `assumption_assurance` + `open_channels` (con el flag non-exhaustive), DESPUÉS el
  `status` y el número SOLO con su caveat de validez; glifo neutro, nunca verde ✓, nunca "PASS".
- **FR-008** `costwright.fusion` sigue **pure-stdlib**: no importa `eleata_verify` ni `numpy` (el `1−x^(1/m)`
  y el `min(1,·)` son `math`/builtins). Verificado por el test de import existente.
- **FR-009** Demo e2e `examples/certified_run_demo.py`: produce un certified run **no-vacuo** (cap casi
  no-binding, k=0) y uno **vacuo** (cap binding), consumiendo
  `eleata_verify.epsilon.interference_risk_bound` como caja negra. Determinista.

## No-objetivos (explícitos)
- NO se prueba ni mecaniza nada nuevo del teorema (E3 ya está; esta feature lo CONSUME).
- NO se cierran los canales 2/3/4 — se DECLARAN abiertos.
- NO se agrega un `--fail-on` a `fuse`: el bundle es un audit record, no una policy de CI.
- NO se mide ε dentro de `costwright.fusion` (vive en eleata-verify, caja negra).

## Council gate — RESUELTO (council-v2, 6 voces / 4 modelos, 2026-06-13)

**Veredicto: GO-con-cambios** (ganador codex/pragmático, líder por directiva). 2 voces (mistral
abogado-del-diablo, groq escéptico) votaron NO-GO = "sacá el bloque del bundle firmado, dejá el
accounting en un CLI separado". **Se RECHAZA el NO-GO**: codex + gemini refutan que sacarlo de la firma
*destruye su valor de auditoría* (un side-claim sin firmar no queda atado al run); la malinterpretación
se neutraliza con degradación semántica + re-cómputo + cuarentena, no eliminando el audit trail. Los P0
convergentes (incorporados al modelo de datos de arriba — esta sección los fija):

- **P0-1 (codex/gemini/todos): matar la palabra `bounded`.** El status nunca lee como aprobación →
  `conditionally_quantified | vacuous | inapplicable`. Es una *cuantificación condicional*, no garantía.
- **P0-2 (codex): self-attestation ≠ assurance.** Separar `assumptions_attested` (QUÉ se declaró) de
  `assumption_assurance` (`self_asserted` default | `evidence_attached` | `independently_reviewed`).
  **NUNCA derivar un estado de "garantía" de `self_asserted`.** El default es `self_asserted` y el status
  más alto posible con self-asserted sigue siendo `conditionally_quantified` (que NO promete nada).
- **P0-3 (codex/gemini): fusion RE-CHEQUEA la aritmética (pure-stdlib).** Como `fusion` es shape-only,
  recomputa `channel1_conditional_risk_upper = min(1, α+ε(1+α)/(c−ε))` de los inputs del caller y exige
  match (mismatch ⇒ `ValueError`). Para **k=0** verifica además `eps_upper == 1−δ_eps^(1/m)` (forma
  cerrada CP, stdlib) ⇒ `eps_upper` queda VERIFICADO. Para k>0, `eps_upper` es *caller-reported*
  (sanity: `k/m ≤ eps_upper ≤ 1`; full-verify requiere el estimador). El campo `bound_verification` lo
  declara. Esto responde "un atacante inyecta ε=1e-10 y obtiene bounded": la aritmética cierra, pero el
  status nunca es garantía y `eps_upper` k>0 queda marcado no-verificado.
- **P0-4 (codex/B/D/F): `pretty()` empieza con `NO JOINT GUARANTEE`**, muestra assurance + open_channels
  ANTES del número, glifo neutro (▲/⚪), nunca verde ✓, y muestra el número SOLO con su caveat.
- **P0-5 (codex/gemini/A/B/D): `open_channels_non_exhaustive: true`** + agregar **canal 5
  policy-awareness/endogenous-drift** (el agente cambia su política porque "sabe" que el budget se
  agota), adaptive-caps, cross-run composition, enforcement/bypass, shared-state, downstream, unknown.
- **P0-6 (codex): cuarentena fuera de `composition`.** El análisis va en una key top-level
  `conditional_analyses` (sibling de `composition`). `composition` queda INTACTO (joint_guarantee:false).
- **P0-7 (codex/gemini): renombrar** `alpha_effective`→`channel1_conditional_risk_upper`,
  `joint_confidence`→`conditional_bound_confidence` (el nombre largo admite su alcance de 1 canal).

El modelo de datos y FR de arriba ya reflejan estos P0. Pendiente: audit-3 antes de mergear; gate humano
"dale" antes de push.
