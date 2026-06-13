# Implementation Plan: certified agent run — the `interference` block (E3/P3)

**Spec**: `spec.md` (this dir) · **Branch**: `004-certified-agent-run` · **Created**: 2026-06-13

## Technical context
- **Repo**: `costwright` (pure-stdlib cost certificates for LLM-agent workflows; zero runtime deps).
- **Dep (black box)**: `eleata-verify` pinned to origin/main @ `b7a2c71` (the additive contract +
  `eleata_verify.epsilon.interference_risk_bound`). Consumed ONLY in the demo/tests, never in the core.
- **Schema**: `costwright.fusion.v1` extended ADDITIVELY with an optional top-level `conditional_analyses`
  key (council 004 decision: v1-additive, not v2).

## Constitution check
- **Zero-dep core preserved**: `costwright.fusion` stays pure-stdlib. The (ii) bound recompute, the
  Clopper-Pearson upper (regularized incomplete beta via `_betacf`/`_betai`), and `1−eta^(1/m)` are all
  `math`/builtins — no `eleata_verify`/`numpy` import. Verified by the existing import test + a new one.
- **Conservative by construction**: every honesty invariant (council P0-1..P0-7) is enforced by `fusion`,
  not trusted from the caller. `composition.joint_guarantee` never moves off `false`.
- **Tamper-evidence**: `fusion_digest` covers the whole bundle incl. `conditional_analyses`.

## Approach (dead-code-first)
1. **Helpers** (`_inflate_alpha`, `_betacf`, `_betai`, `_cp_upper`) — pure-stdlib math; recompute the
   (ii) bound and the CP upper authoritatively. `_cp_upper` fail-safes (→ 1.0 ⇒ vacuous) on any numerical
   failure (never an understated ε).
2. **Builder** `conditional_analysis_from_epsilon(...)` — maps the estimator's dict to the demoted names
   + the caller's attestation (shape-only; no compute).
3. **Validator** `_validate_conditional_analyses(...)` — shape gate (allowlist) → structural cross-checks
   → RECOMPUTE eps/bound/confidence from primitives → DERIVE status → build `out` from the allowlist
   (drops any injected key) → ship authoritative recomputed values.
4. **`fuse(..., conditional_analyses=None)`** — validate before building, embed OUTSIDE `composition`,
   recompute digest.
5. **`pretty()`** — opens `NO JOINT GUARANTEE`; neutral glyph; assurance + open_channels before the number.
6. **Demo** `examples/certified_run_demo.py` — non-vacuous (k=0) + vacuous runs, real estimator black box.
7. **Tests** — `tests_pkg/test_conditional_analyses.py` (pure-stdlib, adversarial) +
   `tests_pkg/test_certified_run_e2e.py` (importorskip the estimator).
8. **Docs** — `CERTIFIED-RUN.md` + FUSION.md/NON-INTERFERENCE.md updates + pin standardization.

## Risks / mitigations
- **Over-claim** (the central risk): mitigated by the council-mandated honesty machinery + audit-3.
- **Numerical correctness of the CP recompute**: verified vs `scipy.stats.beta.ppf` to ≤1.6e-12 over
  m≤1e7 (all k/m) and zero understatement at m up to 1e9; fail-safe for the rest.
- **Coupling to the estimator**: the core never imports it; only the demo/tests do (black box).

## Definition of done
- 94 tests green (57 prior + 37 new), witnesses green, ruff clean on touched files.
- audit-3 (codex-led) on the implementation, all BLOCKERs resolved + numerically verified.
- Docs + pin standardized. Human "dale" before push.
