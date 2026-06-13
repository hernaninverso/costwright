"""Tests del bloque `conditional_analyses` (spec 004, E3/P3): el accounting de ε-interferencia.

Cubre los INVARIANTES DE HONESTIDAD del council 2026-06-13 (GO-con-cambios):
  P0-1 ningún status lee como garantía (no existe 'bounded')
  P0-2 self-attestation ≠ assurance; fusion DERIVA el status, el caller no lo auto-declara
  P0-3 fusion RE-CHEQUEA la aritmética en pure-stdlib (un número inflado/bajo se rechaza)
  P0-4 pretty() abre con NO JOINT GUARANTEE, glifo neutro, canales abiertos antes del número
  P0-5 open_channels no vacío + non_exhaustive forzado
  P0-6 el análisis vive FUERA de composition; joint_guarantee sigue false
  P0-7 nombres degradados (channel1_conditional_risk_upper / conditional_bound_confidence)

Pure-stdlib (no importa eleata_verify): el dict se arma a mano para poder probar rechazos adversarios.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from gasket import fusion  # noqa: E402


# --- helpers ----------------------------------------------------------------------------------------
def _report(category="certifiable"):
    return {"schema": "gasket.v1",
            "units": [{"category": category}],
            "summary": {"total": 1, "vacuous_default_bounds": 0}}


def _vr(**over):
    d = {"verdict": "Supported", "calibrated_confidence": 0.97, "abstain": False,
         "abstain_reason": None, "sla_mode": "balanced", "sla_alpha": 0.05, "sla_certified": True,
         "evidence_cited": "evidence text", "score_outlier_warning": False, "raw_support_prob": 0.9,
         "sufficiency": None, "base_name": "stub", "domain": "demo"}
    d.update(over)
    return d


def _channel1(*, alpha=0.05, m=600, k=0, delta_eps=0.05, coverage=0.80, eps_upper=None,
              attested=("A", "C", "D"), assurance="self_asserted", **over):
    """A well-formed channel1 sub-dict. Default k=0 ⇒ eps_upper is the CP closed form 1−η^(1/m); the
    bound is the EXACT _inflate_alpha so fusion's recheck passes. Override any field to test rejections."""
    if eps_upper is None:
        eps_upper = fusion._cp_upper(k, m, delta_eps)          # the TRUE Clopper-Pearson upper (matches fusion)
    eps_hat = k / m if m > 0 else 0.0
    bound = fusion._inflate_alpha(alpha, eps_upper, coverage)
    d = {
        "kind": "tv-coupling-bound",
        "channel_covered": "budget-cap-distribution-shift (channel 1 of N; N unknown)",
        "source_estimator": "eleata-verify.epsilon.interference_risk_bound",
        "verify_version": "0.1.0",
        "note": fusion.INTERF_NOTE,
        "channel1_conditional_risk_upper": bound,
        "conditional_bound_confidence": 1.0 - 0.05 - delta_eps,
        "alpha_base": alpha,
        "eps_upper": eps_upper,
        "eps_hat": eps_hat,
        "coverage_used": coverage,
        "cap": 5.0,
        "spend_unit": "unit",
        "m": m,
        "k": k,
        "delta": 0.05,
        "delta_eps": delta_eps,
        "assumptions_attested": sorted(set(attested)),
        "assumption_assurance": assurance,
        "assumption_evidence_ref": None,
        "open_channels": list(fusion._OPEN_CHANNELS),
        "disclaimer": "the estimator disclaimer (open channels + assumptions)",
        "warnings": [],
    }
    d.update(over)
    return {"channel1_budget_cap_risk": d}


def _fuse(ca=None, vr=None):
    return fusion.fuse(_report(), vr or _vr(), run_id="R", gasket_version="0.1.0",
                       verify_version="0.1.0", conditional_analyses=ca)


def _reject(ca, vr=None):
    try:
        _fuse(ca, vr)
        return False
    except ValueError:
        return True


# --- happy path + pure-stdlib invariant -------------------------------------------------------------
def test_valid_conditional_analysis_conditionally_quantified():
    b = _fuse(_channel1())
    d = b["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["status"] == "conditionally_quantified"            # P0-1: never 'bounded'
    assert d["status"] in fusion._INTERF_STATUSES               # the status enum has no value that reads as a guarantee
    assert "bounded" not in fusion._INTERF_STATUSES             # the forbidden word is not a valid status
    assert d["bound_verification"].startswith("recomputed:")    # gasket recomputes eps/bound/confidence
    assert d["assumptions_complete"] is True
    assert d["open_channels_non_exhaustive"] is True            # P0-5 forced
    assert 0.05 < d["channel1_conditional_risk_upper"] < 1.0    # small inflation, not vacuous


def test_fusion_is_pure_stdlib():
    # P0/architecture: importing gasket.fusion must NOT pull eleata_verify or numpy (zero-dep core).
    import importlib
    for mod in ("eleata_verify", "numpy"):
        sys.modules.pop(mod, None)
    importlib.reload(fusion)
    assert "eleata_verify" not in sys.modules
    assert "numpy" not in sys.modules


def test_composition_untouched_and_digest_reproducible():
    b = _fuse(_channel1())
    assert b["composition"]["joint_guarantee"] is False         # P0-6: composition intact
    assert "conditional_analyses" not in b["composition"]       # the analysis is a SIBLING, not inside
    saved = b["run"]["fusion_digest"]
    b["run"]["fusion_digest"] = None
    assert fusion.digest(b) == saved                            # tamper-evident, recomputable WITH the block


def test_backward_compatible_null_when_absent():
    b = _fuse(None)
    assert b["conditional_analyses"] is None


# --- status derivation: fusion decides, the caller cannot (P0-2/4) ----------------------------------
def test_status_inapplicable_when_assumption_missing():
    b = _fuse(_channel1(attested=("A", "C")))                   # missing D
    d = b["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["status"] == "inapplicable" and d["assumptions_complete"] is False


def test_status_vacuous_when_eps_ge_coverage():
    # k>0 with a binding cap: eps_upper >= coverage ⇒ bound saturates to 1.0 ⇒ vacuous.
    b = _fuse(_channel1(m=100, k=90, coverage=0.20))
    d = b["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["status"] == "vacuous"
    assert d["channel1_conditional_risk_upper"] == 1.0


def test_status_vacuous_when_bound_saturates_below_coverage():
    # eps_upper < coverage but α + ε(1+α)/(c−ε) ≥ 1 ⇒ still vacuous (the OTHER vacuity cause).
    # pick coverage just above the (recomputed) CP eps so the ratio explodes past 1 while eps < c.
    m, k = 20, 5
    eps_auth = fusion._cp_upper(k, m, 0.05)
    cov = eps_auth + 0.02                                       # eps_auth < cov (NOT the eps≥cov branch)
    d = _fuse(_channel1(m=m, k=k, coverage=cov))["conditional_analyses"]["channel1_budget_cap_risk"]
    assert eps_auth < cov                                       # confirm we exercise the saturation branch
    assert d["status"] == "vacuous" and d["channel1_conditional_risk_upper"] == 1.0


def test_caller_cannot_self_declare_a_better_status():
    # inject a reassuring status + a fake 'bounded'/'safe' — fusion OVERWRITES with the derived one.
    ca = _channel1(attested=("A", "C"), status="totally_safe", assumptions_complete=True)
    b = _fuse(ca)
    d = b["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["status"] == "inapplicable"                        # derived, not the injected 'totally_safe'
    assert d["assumptions_complete"] is False                   # derived, not the injected True


def test_self_asserted_never_promotes_past_conditionally_quantified():
    # the HIGHEST status reachable with self_asserted assurance is conditionally_quantified (never a guarantee).
    d = _fuse(_channel1(assurance="self_asserted"))["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["status"] == "conditionally_quantified"
    assert d["assumption_assurance"] == "self_asserted"


# --- recompute-and-overwrite (audit-3 P0-3): gasket never SHIPS a caller's derived number ------------
def test_caller_bound_number_is_overwritten_not_shipped():
    # gasket recomputes the (ii) bound from the primitives and SHIPS its own value, ignoring whatever the
    # caller put in channel1_conditional_risk_upper (understated OR overstated). No tampered number ships.
    true_bound = fusion._inflate_alpha(0.05, fusion._cp_upper(0, 600, 0.05), 0.80)
    for tampered in (0.001, 0.5):                              # understated and overstated
        ca = _channel1(k=0, m=600)
        ca["channel1_budget_cap_risk"]["channel1_conditional_risk_upper"] = tampered
        d = _fuse(ca)["conditional_analyses"]["channel1_budget_cap_risk"]
        assert abs(d["channel1_conditional_risk_upper"] - true_bound) < 1e-9
        assert d["channel1_conditional_risk_upper"] != tampered


def test_overstated_eps_is_overwritten_with_true_cp():
    # a caller eps_upper ABOVE the true CP (conservative direction) is accepted but OVERWRITTEN with the
    # true (smaller) CP upper — gasket ships its own authoritative ε, not the caller's.
    ca = _channel1(k=0, m=600)
    ca["channel1_budget_cap_risk"]["eps_upper"] = 0.5          # way above the true CP (~0.005)
    d = _fuse(ca)["conditional_analyses"]["channel1_budget_cap_risk"]
    assert abs(d["eps_upper"] - fusion._cp_upper(0, 600, 0.05)) < 1e-9


def test_understated_eps_upper_is_overwritten_with_true_cp():
    # audit-3 (k>0 hole, FINAL design): a caller-reported eps_upper BELOW the true CP is simply OVERWRITTEN
    # with gasket's authoritative recompute — the understated number can NEVER ship.
    d = _fuse(_channel1(m=100, k=20, eps_upper=0.05))["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["eps_upper"] != 0.05
    assert abs(d["eps_upper"] - fusion._cp_upper(20, 100, 0.05)) < 1e-9
    assert d["eps_upper"] > 20 / 100                            # a real CP upper is above the point estimate


def test_eps_upper_is_recomputed_authoritatively():
    # audit-3 fix: the SHIPPED eps_upper is gasket's own CP recompute, not the caller's number.
    m, k, de = 200, 6, 0.05
    d = _fuse(_channel1(m=m, k=k, delta_eps=de))["conditional_analyses"]["channel1_budget_cap_risk"]
    assert abs(d["eps_upper"] - fusion._cp_upper(k, m, de)) < 1e-9
    # and the bound is recomputed from THAT authoritative eps (not the caller's reported bound)
    assert abs(d["channel1_conditional_risk_upper"]
               - fusion._inflate_alpha(0.05, d["eps_upper"], 0.80)) < 1e-9


def test_cp_upper_matches_closed_form_at_k0():
    # k=0 CP upper has the closed form 1−η^(1/m); gasket returns it CONSERVATIVELY (≥ closed form, within a
    # ~1e-9 relative margin — never below, audit-3 codex R7).
    closed = 1.0 - 0.05 ** (1.0 / 500)
    cp0 = fusion._cp_upper(0, 500, 0.05)
    assert cp0 >= closed and (cp0 - closed) < 1e-9 * closed + 1e-12
    for (k, m) in [(1, 50), (5, 200), (10, 100)]:
        cp = fusion._cp_upper(k, m, 0.05)
        assert k / m <= cp <= 1.0                              # a CP upper is always ≥ the point estimate


def test_eps_hat_must_match_k_over_m():
    ca = _channel1(m=100, k=10)
    ca["channel1_budget_cap_risk"]["eps_hat"] = 0.5            # != 10/100
    assert _reject(ca)


def test_k_greater_than_m_rejected():
    ca = _channel1(m=10, k=20, eps_upper=0.9)
    assert _reject(ca)


def test_alpha_base_must_match_risk_sla_alpha():
    ca = _channel1(alpha=0.10)                                  # risk vr has sla_alpha=0.05
    assert _reject(ca, vr=_vr(sla_alpha=0.05))


# --- audit-3 BLOCKER fixes (2026-06-13): allowlist, recomputed confidence, m=0, δ+η<1 ---------------
def test_injected_keys_are_dropped_allowlist():
    # audit-3 BLOCKER: an injected key like {"safe": true} / {"joint_guarantee": true} must NEVER reach
    # the signed bundle (out is built from an allowlist, not dict(d)).
    ca = _channel1()
    ca["channel1_budget_cap_risk"]["safe"] = True
    ca["channel1_budget_cap_risk"]["joint_guarantee"] = True
    ca["channel1_budget_cap_risk"]["guaranteed"] = "yes"
    b = _fuse(ca)
    d = b["conditional_analyses"]["channel1_budget_cap_risk"]
    for ghost in ("safe", "joint_guarantee", "guaranteed"):
        assert ghost not in d
    # and nothing leaked into the canonical bundle / digest
    assert '"safe":true' not in fusion.canonical(b).lower().replace(" ", "")
    assert '"guaranteed"' not in fusion.canonical(b)


def test_confidence_recomputed_not_caller_declared():
    # audit-3 BLOCKER: a caller cannot self-declare conditional_bound_confidence=0.999999.
    ca = _channel1(delta_eps=0.05)                             # δ=0.05 ⇒ true jc = 1−0.05−0.05 = 0.90
    ca["channel1_budget_cap_risk"]["conditional_bound_confidence"] = 0.999999
    d = _fuse(ca)["conditional_analyses"]["channel1_budget_cap_risk"]
    assert abs(d["conditional_bound_confidence"] - 0.90) < 1e-9   # gasket overwrote it


def test_delta_plus_delta_eps_ge_one_rejected():
    # joint confidence 1−δ−η must be positive.
    ca = _channel1()
    ca["channel1_budget_cap_risk"]["delta"] = 0.6
    ca["channel1_budget_cap_risk"]["delta_eps"] = 0.6
    assert _reject(ca)


def test_tiny_delta_eps_rejected_no_understatement_path():
    # audit-3 codex R6: delta_eps < 1e-6 is where the extreme-tail CP upper can be UNDERSTATED in float64
    # (witness: k=1,m=100,δ_eps=1e-20 gave 0.339, true 0.395). Shape gate REJECTS it ⇒ no accepted-input
    # understatement path. (Real ε-confidence 1−δ_eps is 0.9–0.999, so the floor is non-restrictive.)
    for tiny in (1e-20, 1e-16, 1e-9, 9.9e-7):
        ca = _channel1(delta_eps=tiny)
        # the helper computes eps_upper via _cp_upper(k,m,tiny) which may itself be off, but the SHAPE
        # gate rejects on delta_eps before any of that matters.
        assert _reject(ca), f"delta_eps={tiny} must be rejected"


def test_absurd_sample_size_rejected_no_understatement():
    # audit-3 codex R9/R10: beyond m=1e9 the betai CP recompute loses precision and can UNDERSTATE without
    # tripping the fail-safe (witness: m=1e15, k=9e14, δ_eps=1e-6). The CP recompute is verified
    # conservative for m ≤ 1e9; the shape gate REJECTS larger m ⇒ no accepted-input understatement.
    # (m=len(spends): a 1e9-element list is ~8 GB and real ε samples are ≤ ~1e6, so the cap is non-restrictive.)
    assert not _reject(_channel1(m=10**9, k=0))                # exactly the cap: accepted
    assert _reject(_channel1(m=10**9 + 1, k=0))                # just over the cap: rejected
    assert _reject(_channel1(m=9 * 10**14, k=0))               # codex's R10 understatement regime: rejected
    assert _reject(_channel1(m=10**308, k=0))                  # sub-denormal regime: rejected


def test_m_zero_is_vacuous_not_fabricated():
    # audit-3 (codex): m=0 (no uncapped evidence) must NOT yield a usable bound — eps_upper=1.0 ⇒ vacuous.
    d = _fuse(_channel1(m=0, k=0, eps_upper=1.0))["conditional_analyses"]["channel1_budget_cap_risk"]
    assert d["status"] == "vacuous" and d["eps_upper"] == 1.0


# --- shape validation -------------------------------------------------------------------------------
def test_missing_required_key_rejected():
    for key in ("kind", "channel1_conditional_risk_upper", "alpha_base", "eps_upper", "coverage_used",
                "m", "k", "assumptions_attested", "assumption_assurance", "open_channels", "disclaimer"):
        ca = _channel1()
        del ca["channel1_budget_cap_risk"][key]
        assert _reject(ca), f"missing {key} should reject"


def test_bad_enums_rejected():
    bads = [
        ("kind", "something-else"),
        ("assumption_assurance", "trust_me"),
        ("assumptions_attested", ["A", "Z"]),                  # Z not in {A,C,D}
        ("assumptions_attested", "ACD"),                       # a string is not a list of {A,C,D}
        ("open_channels", []),                                 # must be non-empty
        ("coverage_used", 0.0),                                # must be in (0,1]
        ("eps_upper", 1.5),
        ("conditional_bound_confidence", 1.0),                 # must be in (0,1)
        ("k", 1.5),                                            # integer count
    ]
    for key, val in bads:
        ca = _channel1()
        ca["channel1_budget_cap_risk"][key] = val
        assert _reject(ca), f"{key}={val!r} should reject"


def test_missing_channel1_key_rejected():
    assert _reject({"some_other_analysis": {}})
    assert _reject({})


# --- pretty() honesty (P0-4) ------------------------------------------------------------------------
def test_pretty_opens_with_no_joint_guarantee():
    out = fusion.pretty(_fuse(_channel1()))
    assert out.splitlines()[0].startswith("⚠ NO JOINT GUARANTEE")
    assert "joint_guarantee=False" in out


def test_pretty_conditional_is_neutral_never_green():
    out = fusion.pretty(_fuse(_channel1()))
    # the ε-interference line uses a NEUTRAL glyph (▲), never the green ✓ used for a clean cost/risk.
    interf_lines = [ln for ln in out.splitlines() if "ε-INTERFERENCE" in ln]
    assert interf_lines and "✓" not in interf_lines[0]
    assert "CONDITIONAL analysis, NOT a guarantee" in out
    # open channels (non-exhaustive) appear BEFORE the number line
    idx_channels = out.index("open channels (NON-EXHAUSTIVE)")
    idx_number = out.index("channel-1 conditional risk ≤")
    assert idx_channels < idx_number


def test_pretty_vacuous_shows_no_usable_bound():
    out = fusion.pretty(_fuse(_channel1(m=100, k=90, coverage=0.20)))
    assert "NO USABLE BOUND" in out
    assert "channel-1 conditional risk ≤" not in out           # no reassuring number when vacuous


def test_pretty_inapplicable_shows_does_not_apply():
    out = fusion.pretty(_fuse(_channel1(attested=("A",))))
    assert "BOUND DOES NOT APPLY" in out


# --- builder from the estimator's dict (shape only) -------------------------------------------------
def test_builder_rejects_bad_epsilon_dict():
    try:
        fusion.conditional_analysis_from_epsilon({"alpha_base": 0.05}, assumptions_attested=["A", "C", "D"],
                                                 verify_version="0.1.0")
        assert False, "should reject an epsilon dict missing keys"
    except ValueError:
        pass


def test_builder_rejects_string_assumptions():
    eb = {"alpha_base": 0.05, "alpha_effective": 0.06, "joint_confidence": 0.9, "eps_upper": 0.01,
          "eps_hat": 0.0, "coverage_used": 0.8, "cap": 5.0, "m": 600, "k": 0, "delta": 0.05,
          "delta_eps": 0.05, "spend_unit": "unit", "warnings": [], "disclaimer": "d"}
    try:
        fusion.conditional_analysis_from_epsilon(eb, assumptions_attested="ACD", verify_version="0.1.0")
        assert False, "a bare string is a footgun → must reject"
    except ValueError:
        pass


def test_builder_maps_demoted_names():
    eb = {"alpha_base": 0.05, "alpha_effective": 0.0566, "joint_confidence": 0.90, "eps_upper": 0.00498,
          "eps_hat": 0.0, "coverage_used": 0.80, "cap": 5.0, "m": 600, "k": 0, "delta": 0.05,
          "delta_eps": 0.05, "spend_unit": "unit", "warnings": ["w"], "disclaimer": "d"}
    ca = fusion.conditional_analysis_from_epsilon(eb, assumptions_attested=["D", "A", "C"],
                                                  verify_version="0.1.0")
    d = ca["channel1_budget_cap_risk"]
    assert d["channel1_conditional_risk_upper"] == 0.0566       # ← alpha_effective (P0-7 rename)
    assert d["conditional_bound_confidence"] == 0.90            # ← joint_confidence
    assert d["assumptions_attested"] == ["A", "C", "D"]         # sorted, deduped
    assert d["assumption_assurance"] == "self_asserted"         # default


# --- joint honesty across the whole bundle ----------------------------------------------------------
def test_no_aggregate_safety_boolean_even_with_conditional():
    blob = fusion.canonical(_fuse(_channel1())).lower()
    assert "both_independently_certified" not in blob
    assert "\"safe\":true" not in blob and "joint_guarantee\":true" not in blob
    assert "agent is safe" not in blob.replace("does not assert the agent is safe", "")
