# Non-interference of cost and risk certificates — FUTURE WORK (unproven)

> **Status: open problem. This document states a theorem we have NOT proved.** `costwright fusion` ships
> the two certificates as a *cartesian product* (a single audit record) and deliberately makes **no
> joint claim**. Do not read a fused bundle as a composed guarantee. This file explains exactly what
> would have to be true for composition to be sound, and why it is not free.

## 1. The two certificates and their scopes

A fused `costwright.fusion.v1` bundle carries two facts about one agent run:

- **Cost certificate (costwright / Lean).** A *static, ahead-of-time* property of the workflow graph:
  *well-typed ⟹ aggregate spend ≤ declared budget, on **every** trace.* It is a universal,
  per-trace invariant, machine-checked (`typed-resources`, Lean 4, `vsteps_sound`, no `sorry`).
- **Risk certificate (eleata-verify / SGR).** A *per-output, statistical* property of the verifier on
  a population: *selective risk ≤ α with probability ≥ 1−δ over i.i.d. draws **from the calibration
  domain***. It is a guarantee about a distribution, not about this one output, and it is **bounded to
  the domain** the calibrator was fit on (out-of-domain ⇒ abstain / route-to-human).

These are different *kinds* of statement: one is a deterministic invariant over all execution traces;
the other is a frequentist bound over a distribution of inputs. That mismatch is the crux.

## 2. The composition one is tempted to claim (and must not)

The seductive (wrong) reading of a bundle where `cost.status = certifiable` and `risk.status =
answered` is: *"this run is both within budget **and** correct-with-bounded-error — a compound
safety guarantee."* The bundle does **not** assert this. It does not multiply the two confidence
levels, does not assert statistical independence, and does not assert that one certificate's holding
tells you anything about the other's. The honest object is the pair `(cost, risk)`, each within its
own scope — the **cartesian product**, not a product *measure*.

## 3. The non-interference theorem we would need (Hoare-style)

Write the agent as a program `A` operating on a state that includes a budget counter, and the verifier
as `V` that reads the agent's output `o` and external evidence `e` and emits a calibrated, abstaining
verdict. Let `Cost(A)` be the costwright cost predicate and `Risk(V, D_cal)` the SGR risk predicate w.r.t.
the calibration distribution `D_cal`. A Hoare-style **non-interference** statement would read,
informally:

> Running the budget-instrumented agent `A_b` (with cap `b`) and then verifying its output with `V`
> **does not invalidate either certificate**: `A_b` still satisfies `Cost` (trivially — the cap only
> tightens it), **and** the distribution of `(o, e)` that `V` sees under `A_b` is still in the domain
> for which `Risk(V, D_cal)` holds. Formally one wants a frame rule:
> `{Cost(A_b)} A_b ; V {Cost(A_b) ∧ Risk(V, D_cal)}` with the two postconditions **non-interfering**.

The cost half is easy (a cap can only lower spend). **The risk half is the open problem**, because the
budget mechanism acts on exactly the object the risk certificate is conditioned on: the agent's output
distribution.

## 4. Why it is not free — the interference channels

The council (council-v2, 2026-06-13) enumerated the channels through which budget and risk interact.
Each one can break the risk SLA even though each certificate is individually sound.

1. **Budget cap shifts the output distribution (primary channel).** Capping `recursion_limit` /
   `max_turns` / `max_tokens` changes *which* outputs the agent produces — truncated, fewer tool calls,
   early stops. The SLA was calibrated on `D_cal`; the capped agent emits from `D_cal'`. If
   `D_cal' ≠ D_cal`, the i.i.d.-from-domain premise fails and **the SLA no longer holds on the outputs
   you actually ship**. (eleata-verify's `score_outlier_warning` is a *weak* in-band flag for this, not
   a guarantee; the real mitigation is recalibration on the capped agent's own outputs.)
2. **Shared budget consumed by the verifier.** If `V` draws from the *same* budget pool as `A` (e.g. an
   LLM-judge verifier billed against the agent's token cap), verifying forces `A` to truncate to "pay
   for" verification — again shifting the output distribution, and coupling the two certificates that
   the bundle presents as separate.
3. **The agent games the verifier.** Under optimization pressure (RL, prompt-tuning, or a budget that
   rewards short outputs), `A` can learn to emit outputs that the verifier scores as low-risk while
   spending less — Goodharting the risk signal. The certificate measures the verifier's selective risk
   on `D_cal`, not its robustness to an adversarial producer.
4. **Retries, selection, optional stopping, drift, abstention handling.** Best-of-n selection, "retry
   until the verifier answers", or routing abstentions back into the agent all break the exchangeability
   the SGR bound assumes; concept drift erodes `D_cal`'s validity over time independent of budget.

## 5. The deeper reason: operational ≠ statistical

Even a *clean* operational non-interference proof — a Hoare/separation-logic frame rule showing the
budget accounting and the verification step do not write each other's state — would be **necessary but
not sufficient**. The risk certificate is not a per-trace invariant; it is a property of a
**distribution** (exchangeability/i.i.d. with `D_cal`). An operational frame rule talks about single
executions and state separation; it says nothing about whether the *induced distribution* of outputs
under the budget-capped agent remains exchangeable with the calibration sample. Preserving a
statistical guarantee under an intervention (the cap) requires a **distribution-level** argument
(e.g. a coupling showing the cap is non-binding w.h.p., or the calibrator being fit *on the capped
agent itself*, making the outputs in-distribution by construction). That is a strictly stronger and
different obligation than operational non-interference.

## 6. What a real theorem would have to assume (conjecture, not result)

A defensible composition theorem would likely require some subset of:

- **(A) Separate budget pools.** `V` does not consume `A`'s budget (channel 2 closed by construction).
- **(B) Cap non-binding on `D_cal`, or in-distribution calibration.** Either the cap is slack with high
  probability on the calibration distribution (so capping does not change the realized output
  distribution — a coupling argument), **or** the calibrator is fit on `(o, e)` pairs drawn from the
  **budget-capped agent's own** outputs, so `D_cal' = D_cal` by definition.
- **(C) No verifier→policy feedback.** `A`'s policy is fixed independently of `V` during the certified
  run (channel 3 closed); no selection/optional-stopping on `V`'s verdict (channel 4 closed).
- **(D) Stationarity / monitored drift.** `D_cal` remains representative, with periodic recalibration.

**Conjecture (open).** Under (A)–(D), the cartesian product upgrades to a conjunction that holds
jointly: the run is within budget *and* its shipped output carries the selective-risk SLA, with the two
postconditions non-interfering. **We have not proved this.** Proving it (or finding the minimal
sufficient assumptions, or a counterexample to a weaker set) is the research depth of Exp #4.

## 7. Until it is proved

`costwright fusion` reports the two certificates side by side, each scoped, with `joint_guarantee: false`
and the disclaimer + this caveat embedded in **every** bundle. Treat the pair as two separate facts.
Do not multiply, do not infer one from the other, and do not market a fused bundle as a composed or
joint safety guarantee.

## 8. What HAS shipped (spec 004): a single-channel, conditional accounting — not the joint guarantee

The quantitative bound of `docs/non-interference/THEOREM.md` (ii) **is** proved, and its estimator
(`eleata_verify.epsilon`) and the `costwright.fusion` `conditional_analyses` block now let a bundle *carry*
it. This does **not** upgrade the bundle to a joint guarantee, and §1–§7 stand unchanged:

- It quantifies **only channel 1** (the budget cap shifting the output distribution). Channels 2–4 —
  and policy-awareness / endogenous drift, adaptive caps, cross-run composition, and unknown channels —
  remain **open**, listed in `open_channels` with `open_channels_non_exhaustive: true`.
- It is **conditional** on the operational assumptions (A,C,D), which are **caller-self-asserted** and
  *not verifiable from data*; `costwright` records the assurance level and never promotes the status to a
  guarantee on a self-attestation.
- It is **possibly vacuous**: a binding cap saturates the bound to `1.0` (`status: vacuous`), and the
  bundle then says *NO USABLE BOUND* and prescribes recalibrating on the capped agent (B′).
- `composition.joint_guarantee` stays `false`; the analysis lives **beside** `composition`, and
  `costwright.fusion` re-checks its arithmetic (pure stdlib) rather than trusting the caller's number.

See [../CERTIFIED-RUN.md](../CERTIFIED-RUN.md). The full joint guarantee — closing channels 2–4 and
discharging (A)–(D) — remains the open research of §3/§6.
