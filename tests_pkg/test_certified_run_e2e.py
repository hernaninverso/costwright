"""E2E del certified agent run (spec 004): gasket.fusion consume el estimador de ε de eleata-verify
como CAJA NEGRA. Se SALTA si eleata-verify no está instalado (la suite pure-stdlib no lo requiere); en
CI corre contra la dep pineada. Valida que el black-box → builder → fuse cierra y que fusion re-chequea
los números que produjo un estimador REAL (no uno construido a mano)."""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from gasket import fusion  # noqa: E402

# black-box dependency — skip the whole module if it (or numpy) is absent.
epsilon = pytest.importorskip("eleata_verify.epsilon")


def _report():
    return {"schema": "gasket.v1", "units": [{"category": "certifiable"}],
            "summary": {"total": 1, "vacuous_default_bounds": 0}}


def _vr(**over):
    d = {"verdict": "Supported", "calibrated_confidence": 0.9, "abstain": False, "abstain_reason": None,
         "sla_mode": "balanced", "sla_alpha": 0.05, "sla_certified": True, "evidence_cited": "ev",
         "score_outlier_warning": False, "raw_support_prob": 0.9, "sufficiency": 0.8,
         "base_name": "stub", "domain": "demo"}
    d.update(over)
    return d


def _certified(spends, cap, coverage, *, attested=("A", "C", "D")):
    eb = epsilon.interference_risk_bound(alpha=0.05, delta=0.05, spends=spends, cap=cap, coverage=coverage)
    ca = fusion.conditional_analysis_from_epsilon(eb, assumptions_attested=list(attested),
                                                  verify_version="0.1.0")
    b = fusion.fuse(_report(), _vr(), run_id="e2e", gasket_version="0.1.0", verify_version="0.1.0",
                    conditional_analyses=ca)
    return b["conditional_analyses"]["channel1_budget_cap_risk"], b


def test_e2e_non_vacuous_k0_real_estimator():
    # cap above every spend ⇒ k=0 ⇒ the estimator's eps_upper is the CP closed form; gasket recomputes it.
    d, b = _certified([1.0, 2.0, 3.0, 2.5, 1.5] * 200, cap=10.0, coverage=0.80)
    assert d["status"] == "conditionally_quantified"
    assert d["k"] == 0 and d["bound_verification"].startswith("recomputed:")
    # gasket's recomputed CP must AGREE with the real estimator's reported eps_upper (defense-in-depth)
    assert abs(d["eps_upper"] - fusion._cp_upper(0, d["m"], d["delta_eps"])) < 1e-9
    assert 0.05 <= d["channel1_conditional_risk_upper"] < 1.0
    assert b["composition"]["joint_guarantee"] is False        # never a joint guarantee


def test_e2e_vacuous_binding_cap_real_estimator():
    # half the runs exceed the cap ⇒ large eps_upper ⇒ the bound saturates ⇒ vacuous (the honest case).
    d, _ = _certified([1.0] * 200 + [50.0] * 200, cap=5.0, coverage=0.80)
    assert d["status"] == "vacuous"
    assert d["channel1_conditional_risk_upper"] == 1.0


def test_e2e_fusion_overwrites_tampered_bound():
    # tampering the estimator's reported bound has NO effect: gasket recomputes from primitives and ships
    # its own number (never the caller's arithmetic).
    eb = epsilon.interference_risk_bound(alpha=0.05, delta=0.05, spends=[1.0] * 500, cap=10.0,
                                         coverage=0.80)
    eb["alpha_effective"] = 0.001                              # tamper: pretend the cap barely matters
    ca = fusion.conditional_analysis_from_epsilon(eb, assumptions_attested=["A", "C", "D"],
                                                  verify_version="0.1.0")
    d = fusion.fuse(_report(), _vr(), run_id="e2e", gasket_version="0.1.0", verify_version="0.1.0",
                    conditional_analyses=ca)["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["channel1_conditional_risk_upper"] != 0.001       # the tampered number did NOT ship
    assert abs(d["channel1_conditional_risk_upper"]
               - fusion._inflate_alpha(0.05, d["eps_upper"], 0.80)) < 1e-9


def test_e2e_fusion_overwrites_understated_eps():
    # an eps_upper tampered BELOW the true CP upper is OVERWRITTEN by gasket's authoritative recompute —
    # the understated number can never ship (no reliance on a cross-check tolerance).
    eb = epsilon.interference_risk_bound(alpha=0.05, delta=0.05, spends=[1.0] * 480 + [99.0] * 20,
                                         cap=10.0, coverage=0.80)
    m, k, de = eb["m"], eb["k"], eb["delta_eps"]
    eb["eps_upper"] = 0.0001                                   # tamper DOWN, below the true CP upper
    ca = fusion.conditional_analysis_from_epsilon(eb, assumptions_attested=["A", "C", "D"],
                                                  verify_version="0.1.0")
    d = fusion.fuse(_report(), _vr(), run_id="e2e", gasket_version="0.1.0", verify_version="0.1.0",
                    conditional_analyses=ca)["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["eps_upper"] != 0.0001
    assert abs(d["eps_upper"] - fusion._cp_upper(k, m, de)) < 1e-9


def test_e2e_gasket_cp_matches_eleata_verify_cp_for_k_gt_0():
    # cross-validate gasket's pure-stdlib betai CP recompute against eleata-verify's estimator (k>0 path).
    eb = epsilon.interference_risk_bound(alpha=0.05, delta=0.05, spends=[1.0] * 470 + [99.0] * 30,
                                         cap=10.0, coverage=0.80)
    assert eb["k"] > 0                                          # ensure we exercise the bisection path
    assert abs(fusion._cp_upper(eb["k"], eb["m"], eb["delta_eps"]) - eb["eps_upper"]) < 1e-6


def test_cp_upper_conservative_vs_scipy_over_accepted_range():
    # audit-3 codex R6/R7: across the ACCEPTED delta_eps range [1e-6, 0.49], gasket's CP recompute must be
    # conservative (≥ the true Clopper-Pearson upper) — never understated, INCLUDING the k=0 closed form.
    # Reference: scipy Beta.ppf. The threshold is exact (≥ true), not slack — the recompute is provably
    # conservative by construction (return-hi / -expm1 + a relative margin).
    beta = pytest.importorskip("scipy.stats").beta
    for m in (10, 12, 100, 1000, 100000, 10000000):
        for k in {0, 1, 2, max(1, m // 100), max(1, m // 10), max(1, m // 2)}:
            if not 0 <= k < m:
                continue
            for de in (1e-6, 1e-4, 0.001, 0.01, 0.05, 0.1, 0.25, 0.49):
                g = fusion._cp_upper(k, m, de)
                t = float(beta.ppf(1 - de, k + 1, m - k))
                assert g >= t, f"understated at m={m} k={k} delta_eps={de}: {g} < {t}"


def test_cp_upper_k0_conservative_codex_r7_witness():
    # audit-3 codex R7 exact witness: k=0, m=12, delta_eps=1e-6 — the -expm1 closed form rounded ~1 ULP
    # BELOW the true CP upper; the relative margin makes it conservative.
    beta = pytest.importorskip("scipy.stats").beta
    g = fusion._cp_upper(0, 12, 1e-6)
    t = float(beta.ppf(1 - 1e-6, 1, 12))
    assert g >= t, f"k=0 witness understated: {g} < {t}"


def test_cp_upper_k0_underflow_never_zero_codex_r8():
    # audit-3 codex R8: k=0 with m≫1e300 and delta_eps→1 underflows the closed form to 0.0 while the true
    # CP upper is a positive denormal — gasket must NOT ship eps_upper=0 below a positive true value.
    g = fusion._cp_upper(0, 10**308, 0.9999999999999999)
    assert g > 0.0, "k=0 underflow shipped eps_upper=0.0 (understatement of a positive true CP)"
    assert g == __import__("math").ulp(0.0)                     # the smallest positive double, conservative
