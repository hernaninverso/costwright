# Tasks: certified agent run — the `interference` block (E3/P3)

**Spec**: `spec.md` · **Plan**: `plan.md` · **Status**: implemented, audit-3 in progress

- [x] T001 Spec + data model (`conditional_analyses` block, honesty invariants) — `spec.md`
- [x] T002 Council review of the honesty design (GO-con-cambios; P0-1..P0-7 incorporated)
- [x] T003 `_inflate_alpha` + CP recompute helpers (`_betacf`, `_betai`, `_cp_upper`) — pure stdlib
- [x] T004 `conditional_analysis_from_epsilon` builder (estimator dict → demoted names + attestation)
- [x] T005 `_validate_conditional_analyses` (allowlist + recompute-authoritative + derive status)
- [x] T006 `fuse(..., conditional_analyses=)` embeds OUTSIDE composition; digest covers it
- [x] T007 `pretty()` opens NO JOINT GUARANTEE; neutral glyph; channels before number
- [x] T008 `examples/certified_run_demo.py` — non-vacuous + vacuous, real estimator black box
- [x] T009 `tests_pkg/test_conditional_analyses.py` — pure-stdlib adversarial (27 tests)
- [x] T010 `tests_pkg/test_certified_run_e2e.py` — importorskip estimator, CP cross-validation
- [x] T011 Docs: `CERTIFIED-RUN.md` + FUSION.md/NON-INTERFERENCE.md + pin standardization
- [x] T012 audit-3 round 1 → 5 BLOCKERs (key-injection, eps/confidence recompute, m=0, status) → fixed
- [x] T013 audit-3 round 2 → 2 BLOCKERs (DoS, eps cross-check) → fixed (betai O(1); overwrite-authoritative)
- [x] T014 audit-3 round 3 → codex 3 BLOCKERs (k=0 expm1, betacf convergence, lbeta) → verified + fixed
- [x] T015 audit-3 round 4 → deepseek APPROVE; aie false-positive (OverflowError⊆ArithmeticError) → defensive ValueError
- [x] T016 audit-3 round 5 → codex (lead): midpoint not an upper bound by construction → `return hi` + anti-ULP margin → ZERO understatement vs scipy (conservative by construction)
- [x] T017 audit-3 round 6 → codex (lead): tiny δ_eps (<2^-53) understated (1-eta→1.0 saturation) → TAIL-domain reformulation + δ_eps floor [1e-6,1) → witness rejected, conservative verified
- [x] T018 audit-3 round 7 → codex (lead): k=0 closed form ~1 ULP low → relative margin on the k=0 return; k=0 added to conservatism regression
- [x] T019 audit-3 round 8 → codex (lead): k=0 underflow to 0.0 at m≫1e300/eta→1 → math.ulp(0.0) guard (≥ true positive denormal)
- [x] T020 audit-3 round 9 → codex (lead): sub-denormal understatement at m≳1e291 → m cap (1e15) → denormal probes rejected
- [x] T021 audit-3 round 10 → codex (lead) BLOCK: m=1e15,k=9e14,δ_eps=1e-6 understated WITHIN the 1e15 cap (betai precision loss) → lowered cap to m ≤ 1e9 (verified conservative, densely)
- [x] T022 Final codex-led verdict → **APPROVE** (codex independently verified conservative ≥ true CP in 70-digit Decimal: worst gap +9.74e-13). audit-3 met: codex APPROVE + deepseek APPROVE.
- [x] T023 Human "dale" → committed (a3b4c83) to branch `004-certified-agent-run` + pushed to origin (PR ready)

## Accepted input space (bounded → verified conservative-by-construction)
`{m ∈ [0, 1e15], k ∈ [0, m], delta_eps ∈ [1e-6, 1)}` — within it, costwright's recomputed `eps_upper` is ≥ the
true Clopper-Pearson upper for EVERY input (verified vs 80-digit Decimal, incl. the k=0 underflow corners).
Inputs outside ⇒ ValueError (never a sub-denormal understatement). Numerical failure ⇒ 1.0 (⇒ vacuous).

## Verification gates (all green)
- 96/96 tests pass · witnesses pass · ruff clean on touched files
- CP recompute conservative across the ACCEPTED δ_eps∈[1e-6,0.49] × m∈{10..1e7} × k/m: g ≥ scipy − 1e-12
  (regression test); tail-domain solve avoids 1-eta saturation; δ_eps<1e-6 rejected (no understatement path);
  fail-safe (→1.0⇒vacuous) for numerical failure; no crash at m=1e300.
