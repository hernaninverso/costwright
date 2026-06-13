"""gasket.fusion — the "cartesian product of certificates" (spec 003, Exp #4).

A run of an LLM agent can carry TWO INDEPENDENT certificates:

  1. a COST certificate (this repo, gasket.v1): static, ahead-of-time, holds on EVERY trace,
     backed by the Lean 4 cost-soundness theorem (typed-resources, vsteps_sound, no sorry);
  2. a RISK certificate (eleata-verify `verify()`): per-output, a population selective-risk SLA
     (Geifman & El-Yaniv 2017) on i.i.d. data from the calibration domain, or an abstention.

This module BUNDLES the two into a single `gasket.fusion.v1` audit record — their CARTESIAN
PRODUCT — and is deliberately conservative about what that bundle is ALLOWED to claim:

  * it is NOT a composed/joint guarantee (`composition.joint_guarantee` is ALWAYS false);
  * there is NO aggregate "both passed" boolean (it would be read as a green safety badge);
  * the honesty disclaimer + the non-interference caveat ship INSIDE every bundle.

ZERO runtime deps + ZERO knowledge of eleata-verify internals: this module is pure stdlib and only
ever reads the SHAPE of `gasket check --json` and of `eleata_verify.VerifyResult.to_dict()` (the
frozen, additive-only contract in eleata-verify/docs/API-CONTRACT.md). It NEVER imports eleata_verify
— so adding fusion keeps gasket's "no runtime dependencies" guarantee intact (verified by tests).

The non-interference theorem (budget ⊥ risk, Hoare-style) that WOULD justify composing the two is
explicitly FUTURE WORK — see docs/NON-INTERFERENCE.md. Do not sell this bundle as a joint guarantee.
"""
import hashlib
import json
import math

SCHEMA = "gasket.fusion.v1"

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
_COST_CATEGORIES = set(_COST_SEVERITY)                     # the closed gasket.v1 category enum
_RISK_VERDICTS = {"Supported", "Refuted", "Not Enough Evidence", "Conflicting"}
_SLA_MODES = {"strict", "balanced"}


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


# --- cost certificate (from a gasket.v1 report dict) ------------------------------------------------
def cost_certificate(gasket_v1_report: dict, *, gasket_version: str, workflow_digest=None) -> dict:
    """Derive the `cost` block from a gasket.v1 report dict (the output of `gasket check --json`).

    `gasket_version` is the caller's assertion of which gasket produced the report (gasket.v1 does
    not embed it — a known limitation; gasket.v2 may). `workflow_digest` binds the report to the
    actual analyzed artifact (anti-substitution); None when the caller did not provide it.
    """
    if not isinstance(gasket_v1_report, dict):
        raise ValueError("gasket_v1_report must be a dict")
    if gasket_v1_report.get("schema") != "gasket.v1":
        raise ValueError(f"expected schema 'gasket.v1', got {gasket_v1_report.get('schema')!r}")
    if not _is_opt_str(workflow_digest):
        raise ValueError("workflow_digest must be a non-empty string or None")
    units = gasket_v1_report.get("units")
    summary = gasket_v1_report.get("summary")
    if not isinstance(units, list) or not isinstance(summary, dict):
        raise ValueError("gasket.v1 report must have list 'units' and dict 'summary'")
    # gasket.v1 has a CLOSED category enum — reject malformed/unknown units instead of silently
    # dropping them (an unknown category could otherwise hide a runaway and downgrade the status).
    for u in units:
        cat = u.get("category") if isinstance(u, dict) else None
        # isinstance(str) guards the `in set` membership (an unhashable category would raise TypeError).
        if not isinstance(cat, str) or cat not in _COST_CATEGORIES:
            got = cat if isinstance(u, dict) else type(u).__name__
            raise ValueError(f"gasket.v1 unit has missing/unknown category: {got!r}")
    cats = {u["category"] for u in units}
    worst = next((c for c in _COST_SEVERITY if c in cats), None)
    status = worst if worst is not None else "no_graph_units"   # worst is None only when units == []
    return {
        "source": "gasket.v1",
        "gasket_version": str(gasket_version),
        "report_digest": digest(gasket_v1_report),
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


# --- the cartesian product --------------------------------------------------------------------------
def fuse(gasket_v1_report: dict, verify_result_dict: dict, *, run_id: str,
         gasket_version: str, verify_version: str, created_unix=None,
         workflow_digest=None, calibrator_digest=None, claim=None) -> dict:
    """Bundle a cost certificate and a risk certificate into a `gasket.fusion.v1` audit record.

    The two are an independent CARTESIAN PRODUCT — there is NO joint guarantee and NO aggregate
    "both passed" boolean (council 003 P0). `composition.joint_guarantee` is ALWAYS false and the
    disclaimer + non-interference caveat ride inside the bundle. `run_id` binds both certificates to
    the same run; `created_unix` is caller-stamped (None if not provided — kept deterministic, this
    module never reads the clock). `fusion_digest` covers the whole bundle (with fusion_digest and
    signature held null), making the record tamper-EVIDENT.
    """
    if not isinstance(run_id, str) or not run_id.strip():
        raise ValueError("run_id must be a non-empty (non-whitespace) string")
    if created_unix is not None and not (_is_num(created_unix) and created_unix >= 0):
        raise ValueError("created_unix must be a non-negative finite number or None")
    bundle = {
        "schema": SCHEMA,
        "run": {"run_id": run_id, "created_unix": created_unix, "fusion_digest": None},
        "cost": cost_certificate(gasket_v1_report, gasket_version=gasket_version,
                                 workflow_digest=workflow_digest),
        "risk": risk_certificate(verify_result_dict, verify_version=verify_version,
                                 calibrator_digest=calibrator_digest, claim=claim),
        "composition": {
            "kind": "cartesian-product",
            "joint_guarantee": False,
            "disclaimer": DISCLAIMER,
            "non_interference": NON_INTERFERENCE,
        },
        "signature": None,   # reserved: the signed certification layer (certd/E1, non-OSS) fills it
    }
    # fusion_digest and signature are both null at compute time → the digest is reproducible.
    bundle["run"]["fusion_digest"] = digest(bundle)
    return bundle


# --- human output -----------------------------------------------------------------------------------
_RISK_BADGE = {"answered": "✓", "abstained": "↻", "uncertified": "∅"}
_COST_BADGE = {"certifiable": "✓", "default_dependent": "▲", "non_certifiable": "✗",
               "runaway": "‼", "parse_error": "·", "no_graph_units": "·"}


def pretty(bundle: dict) -> str:
    """Human view: the two certificates SIDE BY SIDE (never a single aggregate verdict) + the
    disclaimer, ALWAYS. Conservative by construction (council 003 P0)."""
    c, r, comp = bundle["cost"], bundle["risk"], bundle["composition"]
    s = c.get("summary", {})
    vac = s.get("vacuous_default_bounds", 0)
    out = [
        f"gasket fusion — cartesian product of two SEPARATELY-SCOPED certificates, NOT a joint "
        f"guarantee (schema {bundle['schema']})",
        f"  run: {bundle['run']['run_id']}",
        "",
        f"  {_COST_BADGE.get(c['status'], '?')} COST  ({c['source']}, v{c['gasket_version']})  "
        f"status={c['status']}"
        + (f"  [{vac} vacuous default bound(s)]" if vac else ""),
        f"      scope: {c['scope']}",
        f"      backing: {c['theorem']['mechanized']}",
        f"  {_RISK_BADGE.get(r['status'], '?')} RISK  ({r['source']}, v{r['verify_version']})  "
        f"status={r['status']}  verdict={r['verdict']}  conf={r['calibrated_confidence']}  "
        f"SLA≤{r['sla_alpha']} ({r['sla_mode']}, certified={r['sla_certified']})"
        + ("  ⚠score-outlier" if r["score_outlier_warning"] else ""),
        f"      scope: {r['scope']}",
        "",
        f"  composition: {comp['kind']} — joint_guarantee={comp['joint_guarantee']}",
        f"  ⚠ {comp['disclaimer']}",
        f"  ⚠ non-interference: {comp['non_interference']}",
    ]
    return "\n".join(out)


def dumps(bundle: dict) -> str:
    """Pretty-but-stable JSON for CLI/tooling (matches gasket.report.dumps style); NO NaN/Infinity."""
    return json.dumps(bundle, indent=1, ensure_ascii=False, sort_keys=True, allow_nan=False)
