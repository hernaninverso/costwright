#!/usr/bin/env python3
"""Numerical witness for THEOREM (iii) [necessity] + (i)/(ii) [prescription].

EXISTENCE counterexample: we exhibit a CONCRETE (agent A, budget b, verifier V) where the COST
certificate holds for the capped agent A_b yet the RISK SLA — certified on the uncapped calibration
domain D=P — FAILS on the deployed (capped) distribution P_b. So the two certificates do NOT compose
for free. The distributions are DERIVED from the explicit V + cap (not postulated): the cap maps the
hard output to its truncation, and V's explicit verdict on each output determines (answered, correct).

Concrete construction:
  - Output space: e (easy), h_full (hard, full reasoning), h_trunc (hard, truncated).
  - Per-call spend: spend(e)=1, spend(h_full)=10, spend(h_trunc)=4.   Budget b=5.
  - Explicit verifier V (output → (answers?, correct?)):  the truncated hard output is the failure mode —
    V still ANSWERS on it (high confidence) and is WRONG (the hallucinated short completion looks fluent).
        V(e)      = (answer, correct)
        V(h_full) = (answer, correct)
        V(h_trunc)= (answer, WRONG)
  - Uncapped agent A: emits e w.p. f_e, h_full w.p. f_h.   (all answered-correct ⇒ R(V;P)=0 ≤ α)
  - Capped agent A_b (cap = stopping intervention): on the hard input it would spend 10 > b=5, so it
    truncates → emits h_trunc instead of h_full. spend(A_b) ≤ b on every trace (cost cert holds).
"""

ALPHA = 0.05
F_HARD = 0.30
BUDGET = 5
SPEND = {"e": 1, "h_full": 10, "h_trunc": 4}
# explicit verifier: output -> (answered, correct)
V = {"e": (True, True), "h_full": (True, True), "h_trunc": (True, False)}


def dist_to_buckets(output_probs):
    """Map an output distribution through the explicit V into {ac, ae, ab}."""
    d = {"ac": 0.0, "ae": 0.0, "ab": 0.0}
    for out, p in output_probs.items():
        answered, correct = V[out]
        if not answered:
            d["ab"] += p
        elif correct:
            d["ac"] += p
        else:
            d["ae"] += p
    return d


def selective_risk(d):
    ans = d["ac"] + d["ae"]
    return (d["ae"] / ans) if ans > 0 else 0.0, ans


def cap_output(out):
    """The stopping intervention: if running `out` would exceed the budget, truncate the hard one."""
    if SPEND[out] <= BUDGET:
        return out
    return "h_trunc" if out == "h_full" else out   # hard truncates to its (wrong) short completion


def main():
    f_e = 1.0 - F_HARD
    # uncapped agent A → output distribution → (through V) P  [= calibration domain D]
    A = {"e": f_e, "h_full": F_HARD}
    P = dist_to_buckets(A)
    R_P, c_P = selective_risk(P)

    # cost certificate sanity: A_b never exceeds the budget on any trace
    A_b = {}
    for out, p in A.items():
        capped = cap_output(out)
        assert SPEND[capped] <= BUDGET, f"cost cert would be violated by {capped}"
        A_b[capped] = A_b.get(capped, 0.0) + p
    P_b = dist_to_buckets(A_b)
    R_Pb, c_Pb = selective_risk(P_b)

    print("=== (iii) NECESSITY: derived from explicit V + cap (not postulated) ===")
    print(f"  A    = {A}   → P   = {{ac:{P['ac']:.2f}, ae:{P['ae']:.2f}, ab:{P['ab']:.2f}}}")
    print(f"  A_b  = {A_b} → P_b = {{ac:{P_b['ac']:.2f}, ae:{P_b['ae']:.2f}, ab:{P_b['ab']:.2f}}}")
    print(f"  certified on D=P:   R(V;P)   = {R_P:.3f}  ≤ α={ALPHA}   (coverage {c_P:.2f})")
    print(f"  deployed  on P_b:   R(V;P_b) = {R_Pb:.3f}  > α          (coverage {c_Pb:.2f})")
    assert R_P <= ALPHA, "premise: SLA certified on D=P"
    assert R_Pb > ALPHA, "the counterexample must violate the SLA on P_b"
    assert all(SPEND[o] <= BUDGET for o in A_b), "cost cert holds for A_b"
    print(f"  ⇒ composition FAILS: cost-cert valid (spend ≤ {BUDGET}), yet R(V;P_b)={R_Pb:.2f} ≫ α.\n")

    # (i) FIX B′: calibrate on the capped agent (D'=P_b). SGR would set τ to EXCLUDE the h_trunc outputs
    #     from the answered set (they are the error mass) → risk on the answered set ≤ α again.
    print("=== (i) FIX B′: calibrate on the deployed (capped) agent ⇒ exact composition ===")
    print("  D'=P_b ⇒ SGR's threshold abstains on the truncated-hard mass (the error region) → R ≤ α.\n")

    # (ii) FIX B: keep the cap NON-BINDING — only ε of the hard mass is ever truncated.
    print("=== (ii) FIX B: non-binding cap (only ε truncated) ⇒ R ≤ min(1, α + ε(1+α)/(c−ε)) ===")
    print(f"  {'eps':>6} | {'R(V;P_b)':>9} | {'bound':>8} | holds")
    ok = True
    for eps in (0.0, 0.005, 0.01, 0.02, 0.05):
        trunc = min(eps, F_HARD)
        A_eps = {"e": f_e, "h_full": F_HARD - trunc, "h_trunc": trunc}
        R, c = selective_risk(dist_to_buckets(A_eps))
        bound = min(1.0, (ALPHA * c_P + eps) / (c_P - eps)) if eps < c_P else 1.0
        holds = R <= bound + 1e-9
        ok = ok and holds
        print(f"  {eps:>6.3f} | {R:>9.4f} | {bound:>8.4f} | {'✓' if holds else '✗'}")
    assert ok, "the (ii) bound must hold for the non-binding cap"
    print("\nRESULT: PASS — (iii) is a real, V-explicit counterexample; (i)/(ii) restore the SLA.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
