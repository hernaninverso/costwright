# Non-interference of cost and risk certificates — formal statement (WIP research)

> **WIP research note (spec 003 / Exp #4 follow-up).** This is the formal attempt behind the
> user-facing caveat in [../NON-INTERFERENCE.md](../NON-INTERFERENCE.md). It states the theorem
> precisely, proves the parts that hold, and exhibits a counterexample where composition fails. The
> result is deliberately **modest and honest**: a quantitative degradation bound + a necessity
> counterexample, NOT a heavyweight theorem. Pending council + audit-3 before any claim of being "proved".

## 1. Model

- Output/evidence space `Ω × E`. A claim's correctness is a label `y ∈ {0,1}` (the verdict a verifier
  would emit on `(o,e)` is right/wrong). A **verifier** `V` maps `(o,e) ↦ {answer(verdict) | abstain}`.
- A **distribution** `μ` over `(o,e,y)`. The verifier's **selective risk** and **coverage** on `μ`:
  `cov(V;μ) = μ(V answers)`, `R(V;μ) = μ(V errs ∧ V answers) / μ(V answers)` (0 if coverage 0).
- **Risk certificate (eleata-verify SGR).** Fit on an i.i.d. sample from a **calibration** distribution
  `D` (= `D_cal`). SGR guarantees: with prob `≥ 1−δ` over the sample, `R(V; D) ≤ α`. *This is a property
  of the distribution `D` on which `V` was calibrated.*
- **Agent.** `A` induces a distribution `P` over `(o,e,y)` (uncapped). With a budget cap `b`, the capped
  agent `A_b` induces `P_b`. **Cost certificate (gasket/Lean):** `spend(trace) ≤ b` on every trace of
  `A_b` (static, ahead-of-time).
- **Deployment.** The agent actually ships from `P_dep`. The SLA "holds in deployment" means
  `R(V; P_dep) ≤ α` (with prob `≥ 1−δ`). The SLA was *certified* for `D`. The whole question is whether
  `R(V; D) ≤ α` transfers to `R(V; P_dep)`.

## 2. Assumptions

- **(A) Disjoint budget.** `V`'s computation does not draw from `A`'s budget pool: capping `A` does not
  truncate `V`, and running `V` does not truncate `A`. *(Closes interference channel 2.)*
- **(C) No selection on the verifier (no optional stopping).** `A`'s policy and the shipped `(o,e)` are
  fixed independently of `V`'s verdict. This must EXPLICITLY exclude every downstream channel that makes
  the shipped distribution a function of `V`: **retry-until-answered / retry-on-abstain**, best-of-`n` on
  `V`'s score, fallback, routing, caching keyed on the verdict, and selective publication. Any of these
  makes `P_dep ≠ P_b` and the bound fails. *(Closes channels 3–4; the retry-on-abstain loop is the
  headline failure — council 2026-06-13 P0.)*
- **(D) Cap is a stopping intervention (coupling).** `A_b` runs identically to `A` on shared input and
  shared randomness, and differs ONLY when the cumulative spend would exceed `b` (then it truncates/stops).
  This is what makes the coupling below exact: the two agents agree off the event `{spend > b}`.
- **(B) Non-binding cap, calibrated uncapped.** `D = P` and `P(spend(o) > b) ≤ ε`.
- **(B′) In-distribution calibration.** `D = P_b` (the calibrator was fit on the **capped** agent itself).

Under (A) and (C), the deployment distribution is exactly the capped agent's: `P_dep = P_b`.

## 3. Theorem

**(i) Exact composition under (A),(C),(B′).** `P_dep = P_b = D`, hence `R(V; P_dep) = R(V; D) ≤ α`
with prob `≥ 1−δ`. The cost and risk certificates compose with **no interference**; the SLA holds
verbatim for the deployed capped agent. *Value: not depth — the **prescription** "calibrate on the agent
you actually deploy (capped)" makes composition trivial and exact.*

**(ii) Quantitative non-interference under (A),(C),(D),(B).** By the coupling (D), `A_b` agrees with `A`
off the event `{spend > b}`, so `Pr[X ≠ X_b] ≤ P(spend > b) ≤ ε`, hence `TV(P, P_b) ≤ ε`. For ANY common
measurable event `E` on the joint space `(o, e, verdict, y)`, the definition of total variation gives
`|P(E) − P_b(E)| ≤ TV(P,P_b) ≤ ε` — **unconditionally**. Writing `c = cov(V;P)` and using `R(V;P) ≤ α`:
```
R(V; P_b) = P_b(err ∧ ans) / P_b(ans)
          ≤ (P(err ∧ ans) + ε) / (P(ans) − ε)        [|Δ| ≤ TV ≤ ε on EACH event, separately]
          ≤ (α·c + ε) / (c − ε)
R(V; P_b) ≤ min(1, (α·c + ε)/(c − ε)) = min(1, α + ε(1+α)/(c − ε))   (valid when ε < c)
```
So under a non-binding cap the SLA degrades from `α` to **`min(1, α + ε(1+α)/(c−ε))`** (≈ `α + ε(1+α)/c`
for `ε ≪ c`). **This is the theorem's real content:** a closed-form TV-coupling bound tying the risk
inflation to the cap-binding mass `ε` and the coverage `c`.

> **No "label-invariance" assumption is needed (council dispute, RESOLVED).** Four panelists claimed the
> step needs the cap to preserve labels on the `ε`-mass (and one proposed a weaker bound adding
> `P(abstain)`). That is **wrong**: `TV = sup_E |P(E) − P_b(E)|`, so the per-event bound holds *even if the
> output, verdict, and label all change arbitrarily* within the `ε`-mass — TV already accounts for all
> moved probability. Verified by brute force: **0 violations in 3.2M adversarial trials with label flips in
> the `ε`-mass, minimum slack 0.0000** (the bound is valid AND tight). Codex was right; the majority refuted
> it "con aritmética al revés" — the same lesson as the typed-resources spike. The only requirements are:
> `P, P_b` on the same joint evaluable space, and the coupling (D). A numeric witness ships as
> `examples/noninterference/check_coupling_bound.py`.

**On `ε` (council P0 — corrected):** `ε` does NOT come from the Lean cost certificate. The Lean cert proves
the *cap* (`spend ≤ b` on every trace of `A_b`); the cap-binding mass `ε = P_{o~A}(spend(o) > b)` is a
**distributional** quantity over the *uncapped* agent that needs **independent statistical estimation**
(sample `A`, measure the fraction exceeding `b`). If that estimate holds with confidence `1−η`, the joint
guarantee is `≥ 1 − δ − η`. So the deployment claim is: *risk `≤ min(1, α + ε(1+α)/(c−ε))` with prob
`≥ 1 − δ − η`*.

**(iii) Necessity — composition fails without (B/B′).** There exists `(A, b, V)` with the cost
certificate holding and `R(V; P_b) > α`, when `D = P` but the cap is **binding**. *Construction:* `A`'s
outputs split into an "easy" cluster (short, cheap, `V` correct) and a "hard" cluster (needs long
reasoning, expensive, `V` correct **only with** the full reasoning). The cap `b` truncates the hard
cluster → on `P_b` those become *confidently-answered-but-wrong* → `R(V; P_b) ≫ α` even though
`R(V; P=D) ≤ α`. To be shipped as a concrete numerical witness (a `check_noninterference.py`, in the
spirit of the cost theorem's `check.py`): two clusters, an explicit cap, a calibrated `V`, showing the
selective risk jumps above `α` after capping.

## 4. Why operational non-interference is necessary but NOT sufficient

A Hoare / separation-logic frame rule `{Cost(A_b)} A_b ; V {Cost(A_b) ∧ Risk(V)}` reasons about a
*single* execution's state separation. It can establish (A) (`V` doesn't write `A`'s budget) and (C)
(the output isn't a function of the verdict) — but it says **nothing** about the *distribution* `P_b`
vs `D`. The transfer of the SLA ((i)/(ii)) is a **distribution-level** obligation — assumption (B) or
(B′) — that an operational rule does not deliver. Hence: operational non-interference is *necessary*
(it yields A, C) but *not sufficient* (you still need B/B′). This is the precise sense in which "an
operational result alone does not preserve the statistical SLA".

## 5. Honest scope (kill-criteria for the spike)

- **VIVE iff:** (ii)'s bound is correct and tight enough to be useful, and (iii)'s counterexample is a
  genuine, reproducible witness — i.e., the contribution is "a quantitative composition bound +
  necessity", a clarifying result with a clear engineering prescription (recalibrate on the capped agent;
  or measure `ε` and inflate `α`).
- **MUERE if:** (ii) collapses to vacuity (e.g., the only regime where it's non-trivial requires `ε` so
  small the cap is irrelevant) **and** (iii) is not constructible, leaving nothing beyond the trivial (i).
- **Not claimed:** this is NOT a deep theorem and NOT comparable to the Lean cost-soundness result; no
  Lean mechanization is attempted in this pass (decided: rigorous paper + council/audit; mathlib measure
  theory is out of scope here). Overclaiming scope sinks a note faster than a modest true result stands.

**Council gate (council-v2, 2026-06-13): APRUEBA with caveats — incorporated.** (1) the (ii) bound is
correct WITHOUT label-invariance — verified numerically (§3), rejecting the majority's caveat; (2) (C)
strengthened to exclude retry-on-abstain/selection/optional-stopping (the operative failure); (3) (D)
coupling made explicit; (4) `ε` provenance corrected (independent estimation, joint conf `1−δ−η`); (5)
`min(1,·)` cap added. Remaining work to reach a VIVE verdict: write the full proof of (i)/(ii)/(iii)
cleanly + ship `check_coupling_bound.py` (the (ii) witness, done in spirit above) and
`check_noninterference.py` (the (iii) counterexample) + audit-3.

## 6. Open questions (post-verdict)
- Is `ε` (cap-binding mass) tightly recoverable from gasket's static per-trace bound, or only empirically?
- Does relaxing (C) to "selection independent of `V` but dependent on cost" preserve a (weaker) bound?
- A distribution-shift detector (eleata-verify's `score_outlier_warning`) as an online proxy for `ε`.
