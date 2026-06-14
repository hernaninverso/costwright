"""costwright.fusion — the "cartesian product of certificates" (spec 003, Exp #4).

A run of an LLM agent can carry TWO INDEPENDENT certificates:

  1. a COST certificate (this repo, costwright.v1): static, ahead-of-time, holds on EVERY trace,
     backed by the Lean 4 cost-soundness theorem (typed-resources, vsteps_sound, no sorry);
  2. a RISK certificate (eleata-verify `verify()`): per-output, a population selective-risk SLA
     (Geifman & El-Yaniv 2017) on i.i.d. data from the calibration domain, or an abstention.

This module BUNDLES the two into a single `costwright.fusion.v1` audit record — their CARTESIAN
PRODUCT — and is deliberately conservative about what that bundle is ALLOWED to claim:

  * it is NOT a composed/joint guarantee (`composition.joint_guarantee` is ALWAYS false);
  * there is NO aggregate "both passed" boolean (it would be read as a green safety badge);
  * the honesty disclaimer + the non-interference caveat ship INSIDE every bundle.

ZERO runtime deps + ZERO knowledge of eleata-verify internals: this module is pure stdlib and only
ever reads the SHAPE of `costwright check --json` and of `eleata_verify.VerifyResult.to_dict()` (the
frozen, additive-only contract in eleata-verify/docs/API-CONTRACT.md). It NEVER imports eleata_verify
— so adding fusion keeps costwright's "no runtime dependencies" guarantee intact (verified by tests).

The non-interference theorem (budget ⊥ risk, Hoare-style) that WOULD justify composing the two is
explicitly FUTURE WORK — see docs/NON-INTERFERENCE.md. Do not sell this bundle as a joint guarantee.
"""
import hashlib
import json
import math

SCHEMA = "costwright.fusion.v1"

# --- provenance of the formal backing (NOT a runtime proof; it is the static cost theorem) ----------
THEOREM = {
    "name": "potential-based cost-soundness for LLM-agent workflows (typed-resources)",
    "mechanized": "Lean 4 (vsteps_sound; #print axioms = [propext, Quot.sound]; no sorry)",
    "doi": "10.5281/zenodo.20661092",
    "scope": "well-typed workflow ⟹ aggregate spend ≤ declared budget; static, ahead-of-time, "
             "on every trace of the analyzed graph(s)",
}
RISK_GUARANTEE = {
    "name": "Selective Guaranteed Risk (Geifman & El-Yaniv 2017)",
    "scope": "selective risk ≤ sla_alpha with prob ≥ 1−delta over i.i.d. data FROM THE CALIBRATION "
             "DOMAIN; bounded, NOT universal; out-of-domain ⇒ abstain / route-to-human",
}
COST_SCOPE = "ahead-of-time; static; holds on EVERY trace of the analyzed workflow graph(s)"
RISK_SCOPE = ("per-output; a POPULATION selective-risk SLA on i.i.d. data from the calibration "
              "domain — NOT a guarantee about this single output")

# The honesty boundary travels INSIDE every bundle (council 003 P0: disclaimer non-negotiable).
DISCLAIMER = (
    "This bundle is the CARTESIAN PRODUCT of two SEPARATE, independently-scoped certificates — a "
    "static cost-soundness certificate (holds on every trace, ahead-of-time) and a per-output "
    "selective-risk certificate (a population SLA on i.i.d. data from the calibration domain). It is "
    "a single AUDIT RECORD, NOT a composed guarantee. It does NOT assert the agent is safe; does NOT "
    "establish statistical independence between the two; does NOT multiply or combine their confidence "
    "levels; and does NOT claim that a bounded budget preserves the risk SLA (a budget cap can shift "
    "the agent's output distribution off the calibration domain and invalidate it — see "
    "non_interference). Each certificate holds ONLY within its own scope. Digests here are "
    "tamper-EVIDENCE, not proof of authorship (authenticity needs the signed certification layer)."
)
NON_INTERFERENCE = (
    "UNPROVEN / FUTURE WORK. A Hoare-style non-interference theorem (that budget accounting and "
    "output-risk verification do not invalidate one another under composition) is NOT established. "
    "Known interference channels: (1) a budget cap shifts the output distribution off the risk "
    "calibration domain; (2) a budget shared with the verifier truncates the agent and shifts outputs; "
    "(3) the agent gaming the verifier to spend less; (4) retries / selection / optional-stopping / "
    "drift / downstream handling of abstentions. An operational non-interference result ALONE does NOT "
    "preserve the statistical (i.i.d.) risk SLA. See docs/NON-INTERFERENCE.md."
)

# cost severity, worst → best (the bundle reports the WORST unit category, conservatively).
_COST_SEVERITY = ["runaway", "non_certifiable", "parse_error", "default_dependent", "certifiable"]
_COST_CATEGORIES = set(_COST_SEVERITY)                     # the closed costwright.v1 category enum
_RISK_VERDICTS = {"Supported", "Refuted", "Not Enough Evidence", "Conflicting"}
_SLA_MODES = {"strict", "balanced"}

# --- conditional analyses: the ε-interference accounting (spec 004, E3/P3) --------------------------
# A SINGLE-CHANNEL, CONDITIONAL, possibly-VACUOUS upper bound on selective risk under a budget cap, from
# the TV-coupling non-interference theorem (docs/non-interference/THEOREM.md, ii). It lives OUTSIDE
# `composition` (council 2026-06-13 P0-6) because it is NOT part of the cartesian-product honesty verdict;
# it is a reported analysis the signed bundle BINDS (tamper-evidence) and whose ARITHMETIC fusion
# re-checks in pure stdlib — but whose operational ASSUMPTIONS fusion canNOT verify (self-asserted).
_INTERF_KIND = "tv-coupling-bound"
_CHANNEL_COVERED = "budget-cap-distribution-shift (channel 1 of N; N unknown)"
_SOURCE_ESTIMATOR = "eleata-verify.epsilon.interference_risk_bound"
_ASSURANCE_LEVELS = {"self_asserted", "evidence_attached", "independently_reviewed"}
_ASSUMPTIONS = {"A", "C", "D"}                            # the operational assumptions the (ii) bound needs
# status is NEVER "bounded" (council P0-1: no word that reads as a guarantee). Derived by fusion.
_INTERF_STATUSES = {"conditionally_quantified", "vacuous", "inapplicable"}
# The interference channels this single-channel bound does NOT cover. Non-exhaustive BY CONSTRUCTION.
_OPEN_CHANNELS = [
    "shared-budget: the verifier draws from the agent's budget pool (channel 2)",
    "verifier-gaming: the agent optimizes to be scored low-risk while spending less (channel 3)",
    "retry-on-abstain / best-of-n / optional-stopping / selection on the verdict (channel 4)",
    "policy-awareness / endogenous drift: the agent changes its policy because it knows the budget "
    "shrinks (channel 5)",
    "concept drift over time; adaptive/binding caps; cross-run composition; enforcement bypass / "
    "mis-accounting; shared state or randomness; downstream handling of abstentions; "
    "unknown / unmodeled channels",
]
INTERF_NOTE = (
    "NOT a guarantee. A REPORTED, single-channel, conditional, possibly-vacuous UPPER BOUND on selective "
    "risk under CALLER-SELF-ASSERTED operational assumptions (A,C,D). The signed bundle BINDS it "
    "(tamper-evidence) and RE-CHECKS its arithmetic, but does NOT verify the assumptions and does NOT "
    "estimate ε itself. It covers ONLY the budget-cap distribution-shift channel; see open_channels "
    "(NON-EXHAUSTIVE) and assumption_assurance. It does NOT upgrade composition to a joint guarantee.")


def _inflate_alpha(alpha: float, eps: float, coverage: float) -> float:
    """The (ii) bound, recomputed in PURE STDLIB (mirrors eleata_verify.epsilon.inflate_alpha exactly):
    min(1, α + ε(1+α)/(c−ε)) if ε < c, else 1.0 (saturates — says nothing). Strictly increasing in ε.

    ε is a magnitude (a distribution-shift / CP-upper bound) and is ALWAYS ≥0 in the authoritative path
    (`_cp_upper` never returns <0). A NEGATIVE ε is nonsensical input and would DECREASE α below its base
    (understating the risk — codex r72: `_inflate_alpha(0.2,-0.1,0.9)=0.08 < 0.2`). Clamp ε≥0 so inflation
    never reduces α; α also can't drop below its base (max with alpha) — conservative for any input."""
    eps = max(eps, 0.0)
    if eps >= coverage:
        return 1.0
    return min(1.0, max(alpha, alpha + eps * (1.0 + alpha) / (coverage - eps)))


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta (Lentz's method, Numerical Recipes). O(iterations),
    INDEPENDENT of the sample size — no per-term loop over k (removes the DoS audit-3 flagged). RAISES
    `ArithmeticError` if it does not converge in `maxit` (it stops converging for very large a,b near
    x≈0.5); the caller (`_cp_upper`) treats that as a fail-safe → conservative worst case (audit-3 R3)."""
    fpmin, eps, maxit = 1e-300, 3e-16, 300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c = 1.0
    dd = 1.0 - qab * x / qap
    if abs(dd) < fpmin:
        dd = fpmin
    dd = 1.0 / dd
    h = dd
    for mm in range(1, maxit + 1):
        m2 = 2 * mm
        aa = mm * (b - mm) * x / ((qam + m2) * (a + m2))
        dd = 1.0 + aa * dd
        if abs(dd) < fpmin:
            dd = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        dd = 1.0 / dd
        h *= dd * c
        aa = -(a + mm) * (qab + mm) * x / ((a + m2) * (qap + m2))
        dd = 1.0 + aa * dd
        if abs(dd) < fpmin:
            dd = fpmin
        c = 1.0 + aa / c
        if abs(c) < fpmin:
            c = fpmin
        dd = 1.0 / dd
        delta = dd * c
        h *= delta
        if abs(delta - 1.0) < eps:
            return h
    raise ArithmeticError(f"betacf did not converge (a={a}, b={b}, x={x})")


def _betai(a: float, b: float, x: float) -> float:
    """Regularized incomplete beta I_x(a, b) ∈ [0,1], pure stdlib (Numerical Recipes). Cost is O(1) in the
    sample size. Result clamped to [0,1] (it is a probability; rounding can nudge it out). May raise
    `ArithmeticError` (via `_betacf`) for extreme a,b — the caller fails that SAFE (conservative)."""
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
    bt = math.exp(lbeta + a * math.log(x) + b * math.log1p(-x))
    val = bt * _betacf(a, b, x) / a if x < (a + 1.0) / (a + b + 2.0) else 1.0 - bt * _betacf(b, a, 1.0 - x) / b
    return min(1.0, max(0.0, val))


def _cp_upper(k: int, m: int, eta: float) -> float:
    """One-sided (1−eta) Clopper-Pearson UPPER bound on a Binomial(m, p) proportion with k successes —
    recomputed in PURE STDLIB so costwright NEVER trusts a caller-reported ε (audit-3 2026-06-13 BLOCKER).
    p_U solves the tail equation `P(Binom(m, p_U) ≤ k) = eta`, computed in the TAIL domain as
    `I_{1−p}(m−k, k+1)` vs `eta` directly (NOT `1−eta` — that rounds to 1.0 and understates for δ_eps<2^-53).
    Bisection on `_betai` (cost INDEPENDENT of k/m — no DoS). Verified conservative (≥ true CP) vs scipy
    AND exact-Decimal across δ_eps∈{1e-30..0.49}. Closed form at k=0 (`-expm1` — exact even at m≫1e15).
    k≥m ⇒ 1.0. FAIL-SAFE: any numerical failure ⇒ 1.0 (⇒ vacuous), NEVER an understated ε."""
    if m <= 0 or k >= m:
        return 1.0                                   # no evidence (m=0) or all exceeded ⇒ worst case
    if k == 0:
        # = 1 − eta^(1/m) via -expm1 (no catastrophic cancellation). The closed form can round ~1 ULP
        # BELOW the true value (audit-3 codex R7: k=0,m=12,δ_eps=1e-6 was 6.5e-17 low), so nudge up by a
        # tiny RELATIVE margin (1e-9 — preserves the magnitude of a tiny eps at large m; no absolute floor)
        # to keep it a conservative upper bound BY CONSTRUCTION.
        val = -math.expm1(math.log(eta) / m) * (1.0 + 1e-9)
        # For m ≫ 1e300 with eta→1 the closed form UNDERFLOWS to 0.0 (true CP is a positive denormal, e.g.
        # 1.1e-324) — return the smallest positive double so ε is never reported as exactly 0 below a
        # positive true value (audit-3 codex R8). 0 < true ⇒ ulp(0.0) ≥ true (nearest representable).
        return min(1.0, val) if val > 0.0 else math.ulp(0.0)
    # Work in the TAIL domain (audit-3 codex, round 6): solve P(Binom(m,p) ≤ k) = eta DIRECTLY, comparing
    # the tail probability to `eta` — NOT `_betai(k+1,m−k,p)` vs `1−eta`. For eta < ~2^-53 the `1−eta`
    # form rounds to exactly 1.0 and `_betai` saturates near the top, so the old bisection stopped at the
    # ~1e-16 quantile and MATERIALLY UNDERSTATED the CP upper (witness: k=1,m=100,δ_eps=1e-20 gave 0.339,
    # true 0.395). `P(Binom(m,p) ≤ k) = I_{1−p}(m−k, k+1)` is computed directly (a tiny number compared to a
    # tiny eta — no catastrophic 1−x), and is DECREASING in p. Bracket [k/m, 1]; return the upper end.
    lo, hi = k / m, 1.0
    try:
        for _ in range(100):                        # 100 bisections ≫ 1e-9 precision on [k/m, 1]
            mid = 0.5 * (lo + hi)
            if _betai(m - k, k + 1, 1.0 - mid) > eta:   # tail prob decreasing in p ⇒ too high ⇒ larger p
                lo = mid
            else:
                hi = mid
    except (ArithmeticError, ValueError):
        # ArithmeticError covers OverflowError (math.exp/lgamma) + betacf non-convergence; ValueError
        # covers any math-domain edge. ALL numerical failure ⇒ 1.0 ⇒ vacuous ⇒ never an understated ε.
        return 1.0
    # Return `hi` (NOT the midpoint — audit-3 codex): the bisection invariant is `_betai(hi) ≥ target`, so
    # hi ≥ p_U BY CONSTRUCTION (the midpoint could sit ~1 ULP below p_U). A tiny relative margin (1e-9)
    # then dominates the residual float64 ULP noise between this CF-based `_betai` and an exact reference
    # (≤8e-14 for delta_eps ≥ 1e-3; ≤1e-12 for ≥1e-6) so the result is a CONSERVATIVE upper bound on the
    # true Clopper-Pearson upper for every practical delta_eps. Negligible overstatement (≤1e-9·p, far
    # below any decision threshold), strictly safe: ε is NEVER under-reported. Capped at 1.0. The absolute
    # 1e-12 floor covers the tiny-ε corner where the relative term alone shrinks below the ULP noise.
    return min(1.0, hi * (1.0 + 1e-9) + 1e-12)


# --- canonical JSON + digests -----------------------------------------------------------------------
def canonical(obj) -> str:
    """Deterministic, interoperable JSON: sorted keys, no whitespace, UTF-8, NO NaN/Infinity (those are
    not valid JSON — `allow_nan=False` raises instead of emitting them). The basis for every digest."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(obj) -> str:
    """sha256 of the canonical JSON of `obj`, prefixed `sha256:`. Tamper-EVIDENCE, not authorship."""
    return "sha256:" + hashlib.sha256(canonical(obj).encode("utf-8")).hexdigest()


def digest_text(s: str) -> str:
    """sha256 of a raw text artifact (claim/evidence), prefixed `sha256:`."""
    return "sha256:" + hashlib.sha256(s.encode("utf-8")).hexdigest()


def digest_bytes(b: bytes) -> str:
    """sha256 of raw bytes (exact file content — used for the anti-substitution workflow binding so
    two byte-distinct workflows can never collide), prefixed `sha256:`."""
    return "sha256:" + hashlib.sha256(b).hexdigest()


def _is_bool(x) -> bool:
    return isinstance(x, bool)


def _is_num(x) -> bool:
    # bool is a subclass of int — exclude it. Python ints are arbitrary-precision and always finite
    # (never call math.isfinite on a huge int — it converts to float and raises OverflowError).
    # Floats must be finite (NaN/Infinity are not valid JSON and slip past range checks via NaN < cmp).
    if isinstance(x, bool):
        return False
    if isinstance(x, int):
        return True
    return isinstance(x, float) and math.isfinite(x)


def _is_opt_str(x) -> bool:
    """None, or a non-empty string (binding fields: workflow_digest / calibrator_digest / claim)."""
    return x is None or (isinstance(x, str) and x != "")


# --- cost certificate (from a costwright.v1 report dict) ------------------------------------------------
def cost_certificate(costwright_v1_report: dict, *, costwright_version: str, workflow_digest=None) -> dict:
    """Derive the `cost` block from a costwright.v1 report dict (the output of `costwright check --json`).

    `costwright_version` is the caller's assertion of which costwright produced the report (costwright.v1 does
    not embed it — a known limitation; costwright.v2 may). `workflow_digest` binds the report to the
    actual analyzed artifact (anti-substitution); None when the caller did not provide it.
    """
    if not isinstance(costwright_v1_report, dict):
        raise ValueError("costwright_v1_report must be a dict")
    if costwright_v1_report.get("schema") != "costwright.v1":
        raise ValueError(f"expected schema 'costwright.v1', got {costwright_v1_report.get('schema')!r}")
    if not _is_opt_str(workflow_digest):
        raise ValueError("workflow_digest must be a non-empty string or None")
    units = costwright_v1_report.get("units")
    summary = costwright_v1_report.get("summary")
    if not isinstance(units, list) or not isinstance(summary, dict):
        raise ValueError("costwright.v1 report must have list 'units' and dict 'summary'")
    # costwright.v1 has a CLOSED category enum — reject malformed/unknown units instead of silently
    # dropping them (an unknown category could otherwise hide a runaway and downgrade the status).
    for u in units:
        cat = u.get("category") if isinstance(u, dict) else None
        # isinstance(str) guards the `in set` membership (an unhashable category would raise TypeError).
        if not isinstance(cat, str) or cat not in _COST_CATEGORIES:
            got = cat if isinstance(u, dict) else type(u).__name__
            raise ValueError(f"costwright.v1 unit has missing/unknown category: {got!r}")
    cats = {u["category"] for u in units}
    worst = next((c for c in _COST_SEVERITY if c in cats), None)
    status = worst if worst is not None else "no_graph_units"   # worst is None only when units == []
    return {
        "source": "costwright.v1",
        "costwright_version": str(costwright_version),
        "report_digest": digest(costwright_v1_report),
        "workflow_digest": workflow_digest,
        "scope": COST_SCOPE,
        "summary": summary,
        "worst_category": worst,
        "status": status,
        "theorem": dict(THEOREM),
    }


# --- risk certificate (from an eleata_verify VerifyResult.to_dict()) --------------------------------
# Required keys + validators of the FROZEN eleata-verify contract (additive-only: extras tolerated).
_RISK_REQUIRED = {
    # isinstance(str) guards the `in set` membership: an unhashable value ([]/{}) would otherwise
    # raise TypeError instead of being cleanly rejected as a ValueError (audit-3 R2, codex).
    "verdict": lambda v: isinstance(v, str) and v in _RISK_VERDICTS,   # closed enum (anti-overclaim)
    "calibrated_confidence": lambda v: _is_num(v) and 0.0 <= v <= 1.0,
    "abstain": _is_bool,
    "abstain_reason": lambda v: v is None or isinstance(v, str),
    "sla_mode": lambda v: isinstance(v, str) and v in _SLA_MODES,
    "sla_alpha": lambda v: _is_num(v) and 0.0 < v <= 1.0,
    "sla_certified": _is_bool,
    "evidence_cited": lambda v: isinstance(v, str),
    "score_outlier_warning": _is_bool,
    "raw_support_prob": lambda v: _is_num(v) and 0.0 <= v <= 1.0,
    "sufficiency": lambda v: v is None or _is_num(v),
    "base_name": lambda v: isinstance(v, str),
    "domain": lambda v: isinstance(v, str),
}


def _validate_risk_contract(d: dict) -> None:
    """Strict stdlib validation of the VerifyResult dict (council 003 P0). Required keys present with
    the right type/enum; EXTRA keys tolerated (the contract is additive-only). Raises ValueError with
    the offending key — a breaking upstream change surfaces here instead of corrupting the bundle."""
    if not isinstance(d, dict):
        raise ValueError("verify result must be a dict (VerifyResult.to_dict())")
    missing = [k for k in _RISK_REQUIRED if k not in d]
    if missing:
        raise ValueError(f"verify result missing required key(s): {sorted(missing)}")
    bad = [k for k, ok in _RISK_REQUIRED.items() if not ok(d[k])]
    if bad:
        raise ValueError(f"verify result has invalid value(s) for key(s): {sorted(bad)} "
                         f"(values: {{ {', '.join(f'{k}={d[k]!r}' for k in sorted(bad))} }})")


def _risk_status(d: dict) -> str:
    if not d["sla_certified"]:
        return "uncertified"          # calibrator carries no SGR guarantee ⇒ abstain-all
    if d["abstain"]:
        return "abstained"            # routed to human review
    return "answered"                 # answered within the selective-risk SLA


def risk_certificate(verify_result_dict: dict, *, verify_version: str,
                     calibrator_digest=None, claim=None) -> dict:
    """Derive the `risk` block from an eleata_verify VerifyResult.to_dict() dict.

    `verify_version` is the caller's assertion of the (pinned) eleata-verify version. `calibrator_digest`
    binds the result to the calibrator used (None if not provided). `claim` (the verified claim text) is
    digested for anti-substitution binding; None ⇒ claim_digest null. `evidence_digest` is always
    derivable from the `evidence_cited` the contract guarantees is present.
    """
    if not _is_opt_str(calibrator_digest):
        raise ValueError("calibrator_digest must be a non-empty string or None")
    if not _is_opt_str(claim):
        raise ValueError("claim must be a non-empty string or None")
    _validate_risk_contract(verify_result_dict)
    d = verify_result_dict
    return {
        "source": "eleata-verify.verify",
        "verify_version": str(verify_version),
        "result_digest": digest(d),
        "claim_digest": digest_text(claim) if isinstance(claim, str) else None,
        "evidence_digest": digest_text(d["evidence_cited"]),
        "calibrator_digest": calibrator_digest,
        "scope": RISK_SCOPE,
        "verdict": d["verdict"],
        "calibrated_confidence": d["calibrated_confidence"],
        "abstain": d["abstain"],
        "abstain_reason": d["abstain_reason"],
        "sla_mode": d["sla_mode"],
        "sla_alpha": d["sla_alpha"],
        "sla_certified": d["sla_certified"],
        "score_outlier_warning": d["score_outlier_warning"],
        "domain": d["domain"],
        "evidence_cited": d["evidence_cited"],
        "status": _risk_status(d),
        "guarantee": dict(RISK_GUARANTEE),
    }


# --- conditional analyses: the ε-interference accounting (spec 004) ---------------------------------
def conditional_analysis_from_epsilon(epsilon_bound: dict, *, assumptions_attested,
                                      verify_version: str, assumption_assurance: str = "self_asserted",
                                      assumption_evidence_ref=None) -> dict:
    """Build a `conditional_analyses` dict from an `eleata_verify.epsilon.interference_risk_bound()` dict.

    PURE STDLIB convenience: it only reads the SHAPE of the estimator's output (the additive-only contract)
    and re-labels it with the DEMOTED names (council P0-7) + the caller's attestation. The estimator's
    `alpha_effective`→`channel1_conditional_risk_upper`, `joint_confidence`→`conditional_bound_confidence`.
    fusion re-checks + derives `status` later; here we just assemble the reported block. `status`/
    `assumptions_complete`/`bound_verification`/`open_channels_non_exhaustive` are placeholders that
    `fuse()`/`_validate_conditional_analyses` OVERWRITE (the caller cannot self-declare them).
    """
    if not isinstance(epsilon_bound, dict):
        raise ValueError("epsilon_bound must be a dict (eleata_verify.epsilon.interference_risk_bound())")
    need = ("alpha_base", "alpha_effective", "joint_confidence", "eps_upper", "eps_hat", "coverage_used",
            "cap", "m", "k", "delta", "delta_eps", "spend_unit", "warnings", "disclaimer")
    miss = [k for k in need if k not in epsilon_bound]
    if miss:
        raise ValueError(f"epsilon_bound missing key(s): {sorted(miss)}")
    if isinstance(assumptions_attested, (str, bytes)):    # a bare 'ACD' string is a footgun → reject
        raise ValueError("assumptions_attested must be a list/tuple of {'A','C','D'}, not a string")
    # RECOMPUTE the derived numbers (ε_upper + channel-1 bound) from the PRIMITIVES (k, m, δ_eps, α, c) so the
    # assembler's OUTPUT is authoritative even when used standalone — costwright NEVER ships a caller-reported
    # ε/alpha_effective (codex r72: a caller passing eps_upper=0, alpha_effective=0 with k=9,m=10 must NOT yield
    # 0/0). m capped at 1e9 (beyond it the betai recompute can understate — same guard as the validator).
    _m, _k, _de = epsilon_bound["m"], epsilon_bound["k"], epsilon_bound["delta_eps"]
    _ab, _cu = epsilon_bound["alpha_base"], epsilon_bound["coverage_used"]
    if not (isinstance(_m, int) and not isinstance(_m, bool) and 0 <= _m <= 10**9
            and isinstance(_k, int) and not isinstance(_k, bool) and 0 <= _k <= _m
            and _is_num(_de) and 0.0 < _de < 1.0 and _is_num(_ab) and 0.0 <= _ab <= 1.0
            and _is_num(_cu) and 0.0 < _cu <= 1.0):
        raise ValueError("epsilon_bound primitives (k, m, delta_eps, alpha_base, coverage_used) out of range "
                         "for a Clopper-Pearson recompute (m must be an int in [0, 1e9], k in [0, m])")
    _eps_auth = _cp_upper(_k, _m, float(_de))
    _bound_auth = _inflate_alpha(float(_ab), _eps_auth, float(_cu))
    block = {
        "kind": _INTERF_KIND,
        "channel_covered": _CHANNEL_COVERED,
        "source_estimator": _SOURCE_ESTIMATOR,
        "verify_version": str(verify_version),
        "note": INTERF_NOTE,
        "channel1_conditional_risk_upper": _bound_auth,   # RECOMPUTED, not the caller's alpha_effective
        "conditional_bound_confidence": epsilon_bound["joint_confidence"],
        "alpha_base": epsilon_bound["alpha_base"],
        "eps_upper": _eps_auth,                           # RECOMPUTED Clopper-Pearson upper, not the caller's
        "eps_hat": epsilon_bound["eps_hat"],
        "coverage_used": epsilon_bound["coverage_used"],
        "cap": epsilon_bound["cap"],
        "spend_unit": epsilon_bound["spend_unit"],
        "m": epsilon_bound["m"],
        "k": epsilon_bound["k"],
        "delta": epsilon_bound["delta"],
        "delta_eps": epsilon_bound["delta_eps"],
        "assumptions_attested": sorted(set(assumptions_attested)),
        "assumption_assurance": assumption_assurance,
        "assumption_evidence_ref": assumption_evidence_ref,
        "open_channels": list(_OPEN_CHANNELS),
        "warnings": list(epsilon_bound["warnings"]),
        "disclaimer": epsilon_bound["disclaimer"],
    }
    return {"channel1_budget_cap_risk": block}


# Required keys + validators for the channel1 sub-dict (council-derived fields are NOT required as input
# — fusion overwrites them). Re-check / derivation happens after this shape pass.
_C1_REQUIRED = {
    "kind": lambda v: v == _INTERF_KIND,
    "channel_covered": lambda v: isinstance(v, str) and v != "",
    "source_estimator": lambda v: isinstance(v, str) and v != "",
    "verify_version": lambda v: isinstance(v, str) and v != "",
    "note": lambda v: isinstance(v, str) and v != "",
    "channel1_conditional_risk_upper": lambda v: _is_num(v) and 0.0 <= v <= 1.0,
    "conditional_bound_confidence": lambda v: _is_num(v) and 0.0 < v < 1.0,
    "alpha_base": lambda v: _is_num(v) and 0.0 <= v <= 1.0,
    "eps_upper": lambda v: _is_num(v) and 0.0 <= v <= 1.0,
    "eps_hat": lambda v: _is_num(v) and 0.0 <= v <= 1.0,
    "coverage_used": lambda v: _is_num(v) and 0.0 < v <= 1.0,
    "cap": lambda v: _is_num(v) and v >= 0.0,
    "spend_unit": lambda v: isinstance(v, str) and v != "",
    # m,k are integer counts (exclude bool, which _is_num would otherwise let through via int). m is
    # CAPPED at 1e9 (audit-3 codex R9/R10): it is len(spends), a sample count. The CP recompute is VERIFIED
    # conservative (eps_upper ≥ true CP) vs scipy for m ≤ 1e9 across every k/m × delta_eps; beyond that the
    # betai loses relative precision (a,b ~ m) and can UNDERSTATE without tripping the fail-safe (witness:
    # m=1e15, k=9e14, δ_eps=1e-6). 1e9 is non-restrictive — a list of 1e9 spends is ~8 GB and real ε
    # samples are ≤ ~1e6; an absurd m ⇒ ValueError, never an understatement. k ≤ m is enforced separately.
    "m": lambda v: isinstance(v, int) and not isinstance(v, bool) and 0 <= v <= 10**9,
    "k": lambda v: isinstance(v, int) and not isinstance(v, bool) and v >= 0,
    "delta": lambda v: _is_num(v) and 0.0 < v < 1.0,
    # delta_eps floored at 1e-6 (audit-3 codex R6): below ~1e-12 the extreme-tail CP upper is not
    # reliably computable in float64 (1−eta saturates) and could UNDERSTATE ε. costwright is verified
    # conservative for delta_eps ≥ 1e-8; the 1e-6 floor gives 100× headroom and is non-restrictive
    # (real ε-confidence is 1−δ_eps ∈ [0.9, 0.999]). Below the floor ⇒ ValueError, never an understatement.
    "delta_eps": lambda v: _is_num(v) and 1e-6 <= v < 1.0,
    "assumptions_attested": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v)
                                      and set(v) <= _ASSUMPTIONS,
    "assumption_assurance": lambda v: isinstance(v, str) and v in _ASSURANCE_LEVELS,
    "assumption_evidence_ref": _is_opt_str,
    "open_channels": lambda v: isinstance(v, list) and len(v) > 0 and all(isinstance(x, str) for x in v),
    "disclaimer": lambda v: isinstance(v, str) and v != "",
    "warnings": lambda v: isinstance(v, list) and all(isinstance(x, str) for x in v),
}
_RECHECK_TOL = 1e-9


def _validate_conditional_analyses(ca: dict, risk_block: dict) -> dict:
    """Validate + RE-CHECK + DERIVE the conditional analysis (council 2026-06-13 P0-2/3/4). Returns a
    NORMALIZED copy where fusion (not the caller) sets `status`, `assumptions_complete`,
    `bound_verification`, and forces `open_channels_non_exhaustive=True`. Pure stdlib; raises ValueError
    on any inconsistency so a bad analysis surfaces instead of corrupting the bundle.
    """
    if not isinstance(ca, dict):
        raise ValueError("conditional_analyses must be a dict")
    if "channel1_budget_cap_risk" not in ca:
        raise ValueError("conditional_analyses must contain 'channel1_budget_cap_risk'")
    d = ca["channel1_budget_cap_risk"]
    if not isinstance(d, dict):
        raise ValueError("channel1_budget_cap_risk must be a dict")
    missing = [k for k in _C1_REQUIRED if k not in d]
    if missing:
        raise ValueError(f"channel1_budget_cap_risk missing required key(s): {sorted(missing)}")
    bad = [k for k, ok in _C1_REQUIRED.items() if not ok(d[k])]
    if bad:
        raise ValueError(f"channel1_budget_cap_risk has invalid value(s) for key(s): {sorted(bad)}")

    alpha = d["alpha_base"]
    cov = d["coverage_used"]
    m, k = d["m"], d["k"]
    delta, delta_eps = d["delta"], d["delta_eps"]
    # --- structural cross-checks ---
    if k > m:
        raise ValueError(f"channel1_budget_cap_risk: k={k} > m={m}")
    if m > 0 and abs(d["eps_hat"] - k / m) > _RECHECK_TOL:
        raise ValueError(f"channel1_budget_cap_risk: eps_hat={d['eps_hat']} != k/m={k/m}")
    if delta + delta_eps >= 1.0:                       # else joint confidence 1−δ−η is ≤ 0 (audit-3 codex)
        raise ValueError(f"channel1_budget_cap_risk: delta+delta_eps={delta+delta_eps} >= 1 "
                         f"(joint confidence would be non-positive)")
    # alpha must match the risk certificate's SLA alpha (same α, no incoherent bundle).
    sla_alpha = risk_block.get("sla_alpha")
    if not (_is_num(sla_alpha) and abs(alpha - sla_alpha) <= _RECHECK_TOL):
        raise ValueError(f"channel1_budget_cap_risk: alpha_base={alpha} != risk.sla_alpha={sla_alpha}")

    # --- audit-3 BLOCKER fix: RECOMPUTE every derived number from the PRIMITIVES in pure stdlib and SHIP
    #     the recomputed values (authoritative). costwright NEVER trusts a caller-reported ε / bound /
    #     confidence — a caller can only influence via the measured primitives (k, m, delta_eps, α, c, δ),
    #     which costwright cannot understate arithmetically. (Closes the k>0 understatement + the
    #     self-declared-confidence holes; the underlying spend sample is still the caller's to measure.)
    eps_auth = _cp_upper(k, m, delta_eps)             # Clopper-Pearson UPPER, recomputed (all k)
    # NOTE: the caller's reported eps_upper/bound/confidence are NOT trusted and NOT cross-checked — costwright
    # OVERWRITES them with the values it recomputes here (below), so a reported number cannot understate
    # risk in the signed bundle. (audit-3 2026-06-13: no asymmetric-tolerance cross-check; ship authoritative.)
    # The bound is MONOTONE INCREASING in alpha, and the alpha cross-check above accepts alpha_base within a
    # SYMMETRIC _RECHECK_TOL of the risk cert's sla_alpha — so a caller could pass alpha_base = sla_alpha − tol
    # and shave the recomputed bound by ~tol (an understatement; codex r90). Recompute with the LARGER of the
    # two alphas (both attest the SAME SLA α within tol) ⇒ conservative; ship that alpha so the record verifies.
    alpha_auth = max(alpha, sla_alpha)
    bound_auth = _inflate_alpha(alpha_auth, eps_auth, cov)
    jc_auth = 1.0 - delta - delta_eps
    if not (math.isfinite(bound_auth) and math.isfinite(eps_auth) and 0.0 < jc_auth < 1.0):
        raise ValueError("channel1_budget_cap_risk: recomputed values are not finite / in range")
    bound_verification = ("recomputed: eps_upper via Clopper-Pearson(k,m,delta_eps); bound via TV-coupling "
                          "(ii); confidence 1−delta−delta_eps. costwright does NOT verify the spend sample itself.")

    # --- DERIVE status from the AUTHORITATIVE recomputed values; the caller cannot self-declare it ---
    assumptions_complete = _ASSUMPTIONS <= set(d["assumptions_attested"])
    if not assumptions_complete:
        status = "inapplicable"          # missing an operational assumption ⇒ the (ii) bound does not apply
    elif eps_auth >= cov or bound_auth >= 1.0:
        status = "vacuous"               # the cap degrades the SLA to nothing (says nothing)
    else:
        status = "conditionally_quantified"   # NEVER "bounded"; self_asserted never promotes past this

    # --- ALLOWLIST construction (audit-3 BLOCKER fix): copy ONLY known keys from the caller, so an
    #     injected key like {"safe": true} / {"joint_guarantee": true} can NEVER reach the signed bundle. ---
    out = {key: d[key] for key in _C1_REQUIRED}
    out["alpha_base"] = alpha_auth                    # the conservative alpha the bound is computed at (≥ caller's)
    out["eps_upper"] = eps_auth                       # authoritative (overwrites the caller's)
    out["channel1_conditional_risk_upper"] = bound_auth
    out["conditional_bound_confidence"] = jc_auth
    out["status"] = status
    out["assumptions_complete"] = assumptions_complete
    out["bound_verification"] = bound_verification
    out["open_channels_non_exhaustive"] = True        # forced — the list is non-exhaustive by construction
    # FORCE the honesty/provenance fields to costwright's own constants — the caller cannot inject a
    # `disclaimer="GUARANTEED SAFE"`, a reassuring `note`, a shrunk `open_channels=["none"]`, or a misleading
    # `channel_covered`/`source_estimator` into the signed bundle (codex r78). Only measured PRIMITIVES
    # (k, m, δ_eps, α, c, attestations) come from the caller; every honesty string is costwright's.
    out["kind"] = _INTERF_KIND
    out["channel_covered"] = _CHANNEL_COVERED
    out["source_estimator"] = _SOURCE_ESTIMATOR
    out["note"] = INTERF_NOTE
    out["open_channels"] = list(_OPEN_CHANNELS)
    out["disclaimer"] = NON_INTERFERENCE
    return {"channel1_budget_cap_risk": out}


# --- the cartesian product (+ optional conditional analyses) -----------------------------------------
def fuse(costwright_v1_report: dict, verify_result_dict: dict, *, run_id: str,
         costwright_version: str, verify_version: str, created_unix=None,
         workflow_digest=None, calibrator_digest=None, claim=None,
         conditional_analyses=None) -> dict:
    """Bundle a cost certificate and a risk certificate into a `costwright.fusion.v1` audit record.

    The two are an independent CARTESIAN PRODUCT — there is NO joint guarantee and NO aggregate
    "both passed" boolean (council 003 P0). `composition.joint_guarantee` is ALWAYS false and the
    disclaimer + non-interference caveat ride inside the bundle. `run_id` binds both certificates to
    the same run; `created_unix` is caller-stamped (None if not provided — kept deterministic, this
    module never reads the clock). `fusion_digest` covers the whole bundle (with fusion_digest and
    signature held null), making the record tamper-EVIDENT.

    `conditional_analyses` (spec 004, OPTIONAL): a single-channel, conditional, possibly-vacuous ε-
    interference analysis (see `conditional_analysis_from_epsilon`). It lives OUTSIDE `composition`
    (council P0-6) — `composition.joint_guarantee` stays false regardless. fusion VALIDATES it,
    RE-CHECKS its arithmetic, and DERIVES its `status` (the caller cannot self-declare a guarantee).
    None ⇒ `conditional_analyses: null` (a pure cartesian-product bundle, as before).
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty (non-whitespace) string")
    if created_unix is not None and not (_is_num(created_unix) and created_unix >= 0):
        raise ValueError("created_unix must be a non-negative finite number or None")
    risk = risk_certificate(verify_result_dict, verify_version=verify_version,
                            calibrator_digest=calibrator_digest, claim=claim)
    ca = None if conditional_analyses is None else _validate_conditional_analyses(conditional_analyses, risk)
    bundle = {
        "schema": SCHEMA,
        "run": {"run_id": run_id, "created_unix": created_unix, "fusion_digest": None},
        "cost": cost_certificate(costwright_v1_report, costwright_version=costwright_version,
                                 workflow_digest=workflow_digest),
        "risk": risk,
        "composition": {
            "kind": "cartesian-product",
            "joint_guarantee": False,
            "disclaimer": DISCLAIMER,
            "non_interference": NON_INTERFERENCE,
        },
        # OUTSIDE composition (council P0-6): a reported, conditional, single-channel analysis — NOT part
        # of the cartesian-product verdict and NOT a joint guarantee. null when the caller omits it.
        "conditional_analyses": ca,
        "signature": None,   # reserved: the signed certification layer (certd/E1, non-OSS) fills it
    }
    # fusion_digest and signature are both null at compute time → the digest is reproducible.
    bundle["run"]["fusion_digest"] = digest(bundle)
    return bundle


# --- human output -----------------------------------------------------------------------------------
_RISK_BADGE = {"answered": "✓", "abstained": "↻", "uncertified": "∅"}
# the ✓ for an ANSWERED risk certificate must depend on the VERDICT — a Refuted/Conflicting claim answered
# within the SLA is NOT a green check (it would read as "safe" when the verifier says the claim is false /
# the evidence conflicts — codex r81). Verdict-aware glyph; abstained/uncertified keep their status glyph.
_VERDICT_BADGE = {"Supported": "✓", "Refuted": "✗", "Conflicting": "⚠", "Not Enough Evidence": "▲"}


def _risk_glyph(status: str, verdict: str) -> str:
    if status == "answered":
        return _VERDICT_BADGE.get(verdict, "?")
    return _RISK_BADGE.get(status, "?")
_COST_BADGE = {"certifiable": "✓", "default_dependent": "▲", "non_certifiable": "✗",
               "runaway": "‼", "parse_error": "·", "no_graph_units": "·"}
# NEUTRAL glyphs only — NEVER green ✓ for a conditional analysis (council P0-4: must not read as approval).
_INTERF_BADGE = {"conditionally_quantified": "▲", "vacuous": "∅", "inapplicable": "·"}


def _pretty_conditional(ca: dict) -> list:
    """Render the ε-interference analysis (council P0-4): `NO JOINT GUARANTEE`-framed, with the
    assurance + open-channels caveat BEFORE the number, the number ONLY with its validity caveat, and a
    NEUTRAL glyph (never green). Returns [] when there is no analysis."""
    if not ca or "channel1_budget_cap_risk" not in ca:
        return []
    d = ca["channel1_budget_cap_risk"]
    st = d["status"]
    num = d["channel1_conditional_risk_upper"]
    # The number is a VALID upper bound only when conditionally_quantified; otherwise show why it isn't.
    if st == "conditionally_quantified":
        num_line = (f"      channel-1 conditional risk ≤ {num}  (conf ≥ {d['conditional_bound_confidence']}; "
                    f"α_base={d['alpha_base']}, ε_upper={d['eps_upper']}, c={d['coverage_used']}) "
                    f"— ONLY under the assumptions below, ONLY channel 1")
    elif st == "vacuous":
        num_line = (f"      NO USABLE BOUND — the cap degrades the SLA to nothing: the (ii) bound "
                    f"saturates to 1.0 (ε_upper={d['eps_upper']}, c={d['coverage_used']}); recalibrate "
                    f"on the capped agent (B′) or relax the cap / collect more uncapped runs")
    else:  # inapplicable
        num_line = (f"      BOUND DOES NOT APPLY — operational assumptions incomplete "
                    f"(attested {d['assumptions_attested']}); the reported number is not a valid bound")
    return [
        "",
        f"  {_INTERF_BADGE.get(st, '?')} ε-INTERFERENCE (channel-1 budget-cap, {d['source_estimator']}) "
        f"— a CONDITIONAL analysis, NOT a guarantee",
        f"      status={st}  ·  assumption_assurance={d['assumption_assurance']}  ·  "
        f"verification={d['bound_verification']}",
        f"      ⚠ open channels (NON-EXHAUSTIVE) this does NOT cover: {len(d['open_channels'])} listed — "
        f"e.g. {d['open_channels'][0]}",
        num_line,
        f"      ⚠ {d['disclaimer']}",
    ]


def pretty(bundle: dict) -> str:
    """Human view: the two certificates SIDE BY SIDE (never a single aggregate verdict) + the
    disclaimer, ALWAYS. Conservative by construction (council 003 P0). Opens with NO JOINT GUARANTEE
    (council 004 P0-4) so no reader mistakes the bundle — or the optional ε-interference analysis — for
    a composed safety verdict."""
    c, r, comp = bundle["cost"], bundle["risk"], bundle["composition"]
    s = c.get("summary", {})
    vac = s.get("vacuous_default_bounds", 0)
    out = [
        "⚠ NO JOINT GUARANTEE — costwright fusion bundles SEPARATELY-SCOPED certificates; it is an AUDIT "
        "RECORD, not a composed safety verdict.",
        f"costwright fusion (schema {bundle['schema']})",
        f"  run: {bundle['run']['run_id']}",
        "",
        f"  {_COST_BADGE.get(c['status'], '?')} COST  ({c['source']}, v{c['costwright_version']})  "
        f"status={c['status']}"
        + (f"  [{vac} vacuous default bound(s)]" if vac else ""),
        f"      scope: {c['scope']}",
        f"      backing: {c['theorem']['mechanized']}",
        f"  {_risk_glyph(r['status'], r['verdict'])} RISK  ({r['source']}, v{r['verify_version']})  "
        f"status={r['status']}  verdict={r['verdict']}  conf={r['calibrated_confidence']}  "
        f"SLA≤{r['sla_alpha']} ({r['sla_mode']}, certified={r['sla_certified']})"
        + ("  ⚠score-outlier" if r["score_outlier_warning"] else ""),
        f"      scope: {r['scope']}",
    ]
    out += _pretty_conditional(bundle.get("conditional_analyses"))
    out += [
        "",
        f"  composition: {comp['kind']} — joint_guarantee={comp['joint_guarantee']}",
        f"  ⚠ {comp['disclaimer']}",
        f"  ⚠ non-interference: {comp['non_interference']}",
    ]
    return "\n".join(out)


def dumps(bundle: dict) -> str:
    """Pretty-but-stable JSON for CLI/tooling (matches costwright.report.dumps style); NO NaN/Infinity."""
    return json.dumps(bundle, indent=1, ensure_ascii=False, sort_keys=True, allow_nan=False)
