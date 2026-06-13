#!/usr/bin/env python3
"""Numerical witness for THEOREM (ii) — the TV-coupling non-interference bound.

Claim (docs/non-interference/THEOREM.md, part ii): if TV(P, P_b) ≤ ε then
    R(V; P_b) ≤ min(1, (α·c + ε)/(c − ε)),   c = cov(V;P),  α ≥ R(V;P),   ε < c.

The council split on whether this needs a "label-invariance on the ε-mass" assumption. It does NOT:
`TV = sup_E |P(E) − P_b(E)|`, so the per-event bound holds even if the cap changes the output, the
verdict, AND the correctness label arbitrarily within the ε-mass. This script confirms it by brute
force: it builds adversarial P_b that redistribute up to ε of mass to MAXIMIZE the selective risk
(including correct→error and abstain→error label flips), and checks the bound is never violated.

Run:  python examples/noninterference/check_coupling_bound.py
Pure stdlib (no numpy). Deterministic (seeded).
"""
import random

# joint outcome space: 'ac'=answered&correct, 'ae'=answered&error, 'ab'=abstain.
TRIALS = 400_000
SUBSPLITS = 8          # per P, try several ways to spend the ε budget adversarially


def selective_risk(d):
    ans = d["ac"] + d["ae"]
    return (d["ae"] / ans if ans > 0 else 0.0), ans


def run():
    rng = random.Random(0)
    violations = 0
    worst_slack = 1e9
    checked = 0
    for _ in range(TRIALS):
        a, b, c = rng.random(), rng.random(), rng.random()
        s = a + b + c
        P = {"ac": a / s, "ae": b / s, "ab": c / s}
        alpha, cP = selective_risk(P)                       # take α = R(V;P) (tightest premise)
        if cP <= 1e-9:
            continue
        eps = rng.uniform(0.0, min(0.4, cP * 0.95))         # ε < c
        if cP - eps <= 0:
            continue
        for _ in range(SUBSPLITS):
            # spend ≤ ε of mass adversarially: d1 → 'ae' (raise numerator+denominator),
            # d2: move 'ac' → 'ab' (lower denominator). Total moved ≤ ε ⇒ TV(P,P_b) ≤ ε.
            d1 = rng.uniform(0, eps)
            d2 = eps - d1
            Pb = dict(P)
            take = min(d1, Pb["ab"]); Pb["ab"] -= take; Pb["ae"] += take          # abstain→error flip
            rem = d1 - take; take2 = min(rem, Pb["ac"]); Pb["ac"] -= take2; Pb["ae"] += take2  # correct→error flip
            take3 = min(d2, Pb["ac"]); Pb["ac"] -= take3; Pb["ab"] += take3        # drop coverage
            Rb, _ = selective_risk(Pb)
            bound = min(1.0, (alpha * cP + eps) / (cP - eps))
            checked += 1
            if Rb > bound + 1e-9:
                violations += 1
            worst_slack = min(worst_slack, bound - Rb)
    print(f"checked   = {checked:,} adversarial (P, P_b) pairs (label flips allowed in the ε-mass)")
    print(f"violations= {violations}")
    print(f"min slack = {worst_slack:.6f}   (0 ⇒ the bound is TIGHT — achieved in the worst case)")
    ok = violations == 0
    print("RESULT:", "PASS — bound holds with NO label-invariance assumption" if ok else "FAIL")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(run())
