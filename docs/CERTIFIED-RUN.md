# Certified agent run — cost ⊕ risk ⊕ ε-interference (E3/P3)

> **Experimental** (spec `004-certified-agent-run`, Ronda 2 of the eleata-verify roadmap, Exp #4). A
> *certified agent run* is a `gasket.fusion.v1` bundle that carries the cost certificate, the risk
> certificate, **and** a single-channel, conditional ε-interference analysis — the product side of the
> non-interference theorem (`docs/non-interference/THEOREM.md`). It is **NOT a joint guarantee.** Read
> [FUSION.md](./FUSION.md) and [NON-INTERFERENCE.md](./NON-INTERFERENCE.md) first.

## What it answers

| Question | Certificate | Kind |
|---|---|---|
| *Will this blow my budget?* | **cost** — `gasket check` | static, ahead-of-time, **every trace**, Lean-backed |
| *Can I trust this output, or send it to a human?* | **risk** — `eleata_verify.verify()` | per-output, i.i.d. **population** SLA, domain-bounded |
| *How much can the budget cap degrade the risk SLA?* | **ε-interference** — `eleata_verify.epsilon.interference_risk_bound()` | **conditional, single-channel, possibly vacuous** |

The first two are the cartesian product (FUSION.md). The third is new in spec 004: it makes the
*interaction* between the two **measurable instead of hand-waved** — but only for one channel, only
under self-asserted assumptions, and sometimes it says nothing at all (and says so).

## The theorem behind the third row (one paragraph)

A budget cap `b` changes *which* outputs the agent emits (it truncates the expensive ones). The risk
SLA was calibrated on the *uncapped* distribution `D`; the capped agent ships from `P_b`. If they
differ, the SLA need not hold on what you actually ship — **interference channel 1**. The TV-coupling
bound (`THEOREM.md`, ii) quantifies it: with the *cap-binding mass* `ε = P(spend > b)` bounded by
`eps_upper` at confidence `1−η`, the deployed selective risk degrades from `α` to

```
min(1, α + eps_upper·(1+α)/(c − eps_upper))      (c = coverage)
```

with joint confidence `1−δ−η` (a union bound — **no independence assumed**). ε is measured by sampling
the *uncapped* agent's spends and counting exceedances (Clopper-Pearson upper bound). This is proven
(0 violations in 3.2M adversarial trials, tight) and the counterexample (iii) shows it is *necessary*:
without a non-binding cap or recalibration on the capped agent, composition genuinely fails.

## What the `conditional_analyses` block does and does NOT claim

It lives **outside** `composition` (which stays `joint_guarantee: false`). It is a *reported analysis*
that the signed bundle binds (tamper-evidence) and whose **arithmetic gasket re-checks in pure stdlib**
— but whose operational assumptions gasket **cannot** verify.

**Does claim** (only when `status == conditionally_quantified`): *under the caller-attested operational
assumptions (A,C,D) and the measured ε, the deployed selective risk for channel 1 is ≤
`channel1_conditional_risk_upper` with confidence ≥ `conditional_bound_confidence`.*

**Does NOT claim:**
- It is **not** a joint/composed guarantee. `composition.joint_guarantee` is always `false`.
- It covers **one** interference channel (the budget cap). `open_channels` lists the others —
  shared-budget verifier, the agent gaming the verifier, retries/selection/optional-stopping,
  **policy-awareness / endogenous drift** (the agent shrinking spend *because* it knows the cap),
  concept drift, adaptive caps, cross-run composition, enforcement bypass, unknown channels — and is
  flagged `open_channels_non_exhaustive: true`. **It is not an exhaustive list.**
- The assumptions are **self-asserted** by default (`assumption_assurance`). gasket records *what* was
  attested and *how assured* it is; **it never promotes the status to a guarantee on the strength of a
  self-attestation.** The highest status reachable with `self_asserted` is `conditionally_quantified`.
- The number can be **vacuous**. If the cap is binding (`eps_upper ≥ c`, or the bound saturates to
  `1.0`), `status = vacuous` and the bundle says *NO USABLE BOUND* — the cap degrades the SLA to
  nothing; recalibrate on the capped agent (B′) or relax the cap.

## How gasket keeps the block honest (the anti-overclaim machinery)

`gasket.fusion` is pure-stdlib (it never imports `eleata_verify`/`numpy`); it validates the block by
shape **and**:

1. **Recomputes ε itself (all k).** gasket recomputes the **Clopper-Pearson upper** `eps_upper` from the
   primitives `(k, m, delta_eps)` in pure stdlib (closed form at k=0; the regularized incomplete beta via
   a continued fraction otherwise — cost independent of the sample size) and **ships its own value**,
   discarding the caller's. The recompute is **conservative by construction**: it returns the upper
   bracket plus a tiny margin that dominates float-ULP noise, so `eps_upper` is provably ≥ the true CP
   upper (verified: zero understatement vs `scipy.stats.beta.ppf` across m∈{2..1e7}×k/m×delta_eps∈{1e-6..0.49}).
   On any numerical failure it **fails safe to 1.0** (⇒ vacuous), never an understated ε.
2. **Recomputes the bound and the confidence too.** `channel1_conditional_risk_upper = min(1, α+ε(1+α)/(c−ε))`
   and `conditional_bound_confidence = 1−δ−η` are recomputed from the primitives and shipped authoritative —
   the caller's reported derived numbers are discarded, so a caller cannot report a reassuringly-low bound.
3. **Derives the status itself.** `inapplicable` (an assumption unattested) / `vacuous` / never trusts a
   caller-supplied status; the word `bounded` is not a valid status (council 2026-06-13 P0-1).
4. **Cross-checks** `alpha_base == risk.sla_alpha`, `eps_hat == k/m`, `k ≤ m`, `δ+η < 1`; builds the block
   from an **allowlist** so an injected key (`{"safe": true}`) can never reach the signed bundle.
5. **`pretty()` opens with `NO JOINT GUARANTEE`**, shows the assurance + the open channels *before* the
   number, uses a neutral glyph (never the green ✓ of a clean certificate), and shows the number only
   with its validity caveat.

## Run it

```bash
pip install -e ~/eleata-verify numpy        # pinned black box (origin/main @ b7a2c71: contract + epsilon)
python examples/certified_run_demo.py        # prints a NON-VACUOUS run and a VACUOUS run, side by side
python examples/certified_run_demo.py --json # the full gasket.fusion.v1 record
```

`examples/noninterference/check_coupling_bound.py` (the (ii) bound, 0 violations / tight) and
`check_noninterference.py` (the (iii) necessity counterexample) are the numerical witnesses for the
theorem the block consumes.

## Honesty boundary, restated (the program's meta-lesson)

Three windows each corrected their own over-claim (A the compression, B the 1% wall, C the ε). Now ε is
bounded — but the bound is **single-channel, conditional on self-asserted assumptions, and possibly
vacuous**, and the bundle says so loudly. A certified run is a *better audit record*, not a clean
guarantee. If a reader walks away thinking "the run is safe now," the design failed — which is exactly
why `composition.joint_guarantee` never moves off `false` and the analysis is quarantined beside it.
