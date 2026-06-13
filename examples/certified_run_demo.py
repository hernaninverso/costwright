#!/usr/bin/env python3
"""End-to-end demo of a CERTIFIED AGENT RUN (spec 004, E3/P3) — cost + risk + ε-interference accounting.

  demonstration_only — proves the WIRING of the certified-run artifact (cost cert ⊕ risk cert ⊕ the
  single-channel ε-interference analysis), NOT the utility of any production risk model or a production
  SLA. The base verifier is the trivial lexical-overlap stub from fusion_demo (offline, no GPU/torch).

A "certified agent run" answers three things in one tamper-evident record:
  1. COST  — `gasket check` proves spend ≤ b on EVERY trace (static, Lean-backed).
  2. RISK  — eleata-verify `verify()` gives a per-output selective-risk SLA (or abstains).
  3. ε-INTERFERENCE — *how much can the budget cap degrade the risk SLA?* We measure the cap-binding
     mass ε = P(spend > b) from a sample of the UNCAPPED agent's spends and inflate α via the
     TV-coupling non-interference bound (docs/non-interference/THEOREM.md, ii), consumed as a BLACK BOX
     from `eleata_verify.epsilon.interference_risk_bound`. This is a CONDITIONAL, SINGLE-CHANNEL,
     possibly-VACUOUS analysis — NOT a joint guarantee. `gasket.fusion` re-checks its arithmetic and
     derives its status; it lives OUTSIDE `composition`, which stays `joint_guarantee: false`.

Two runs are printed, because the HONEST half of the story is the vacuous one:
  (A) NON-VACUOUS — a loose cap (no observed exceedances, k=0): α degrades only slightly; the analysis
      is `conditionally_quantified` (under self-asserted (A)(C)(D), channel 1 only).
  (B) VACUOUS — a binding cap (many exceedances): the bound saturates to 1.0 → `vacuous`. The bundle
      refuses to pretend the run is safe and prescribes recalibrating on the capped agent (B′).

eleata-verify is a PINNED BLACK-BOX dependency (window A owns the contract; additive-only). The pin is
origin/main @ b7a2c71 (the additive contract + the ε estimator `eleata_verify.epsilon`):
    pip install -e ~/eleata-verify        # local (private repo)
    pip install numpy
See examples/requirements.txt.

Run:   python examples/certified_run_demo.py [--json]
"""
import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SRC = REPO / "src"
sys.path.insert(0, str(SRC))

from gasket import __version__ as GASKET_VERSION       # noqa: E402
from gasket import fusion                               # noqa: E402

try:
    from eleata_verify import CallableVerifier, RawVerdict, fit, verify
    from eleata_verify import __version__ as EVERIFY_VERSION
    from eleata_verify.epsilon import interference_risk_bound   # the ε black box (window A's contract)
except ImportError as e:                                # pragma: no cover
    sys.exit("eleata-verify not installed (need the epsilon module) — see the header / "
             f"examples/requirements.txt: pip install -e ~/eleata-verify numpy   ({e})")


# --- COST: gasket check over the demo workflow ------------------------------------------------------
def gasket_cost_report(path: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    r = subprocess.run([sys.executable, "-m", "gasket.cli", "check", str(path), "--json"],
                       capture_output=True, text=True, env=env)
    if r.returncode == 2:
        sys.exit(f"gasket check failed (infra): {r.stderr}")
    return json.loads(r.stdout)


# --- RISK: the deterministic lexical-overlap stub + synthetic calibrator (from fusion_demo) ---------
def _overlap_base():
    def fn(claim: str, evidence: str) -> RawVerdict:
        cw = re.findall(r"\w+", claim.lower())
        ew = set(re.findall(r"\w+", evidence.lower()))
        if not cw:
            return RawVerdict("Not Enough Evidence", 0.5)
        j = sum(1 for w in cw if w in ew) / len(cw)
        if j >= 0.5:
            return RawVerdict("Supported", min(0.99, 0.55 + 0.44 * j))
        return RawVerdict("Not Enough Evidence", round(0.55 + 0.30 * j, 4))
    return CallableVerifier(fn, name="lexical-overlap-stub(demonstration_only)")


def _synthetic_calibrator():
    import numpy as np
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.5, 0.99, size=800)
    p_correct = np.clip(1.35 * (raw - 0.5) / 0.49, 0.0, 1.0)
    correct = (rng.random(800) < p_correct).astype(int)
    return fit(raw, correct, domain="demo-synthetic", seed=0)


# --- the UNCAPPED agent's per-run spend sample (synthetic, deterministic, demonstration_only) -------
def _spend_sample():
    """A deterministic, reproducible sample of per-run spends of the agent WITHOUT the cap. In a real
    certified run this is measured by replaying the agent uncapped and recording spend per run."""
    import numpy as np
    rng = np.random.default_rng(7)
    # most runs are cheap (~2-4 units); a heavy tail of a few expensive runs (the ones a cap would bite).
    base = rng.gamma(shape=2.0, scale=1.5, size=1000)          # ~mean 3, right-skewed
    tail = rng.uniform(8.0, 16.0, size=20)                     # 2% genuinely expensive
    return np.concatenate([base, tail]).tolist()


# --- one certified run for a (claim, evidence, cap) -------------------------------------------------
# Representative coverage of the verifier's SLA. In production this is the SLA's MEASURED coverage; if it
# was estimated, pass a conservative LOWER bound (the estimator widens the union bound to cover it).
COVERAGE = 0.80
ALPHA, DELTA, DELTA_EPS = 0.05, 0.05, 0.05


def certified_run(cost_report, base, calibrator, calib_digest, claim, evidence, *, cap, run_id):
    # RISK
    vr = verify(claim, evidence, base, calibrator, mode="balanced")
    # ε-INTERFERENCE: estimate eps_upper from the uncapped spend sample, inflate α (BLACK BOX).
    eb = interference_risk_bound(alpha=ALPHA, delta=DELTA, spends=_spend_sample(), cap=cap,
                                 coverage=COVERAGE, delta_eps=DELTA_EPS, spend_unit="unit")
    # Attest the operational assumptions (A)(C)(D). HERE they are self_asserted — fusion records that
    # and NEVER promotes the status to a guarantee. A real deployment would attach evidence / review.
    ca = fusion.conditional_analysis_from_epsilon(
        eb, assumptions_attested=["A", "C", "D"], verify_version=EVERIFY_VERSION,
        assumption_assurance="self_asserted")
    return fusion.fuse(cost_report, vr.to_dict(), run_id=run_id, gasket_version=GASKET_VERSION,
                       verify_version=EVERIFY_VERSION, calibrator_digest=calib_digest, claim=claim,
                       conditional_analyses=ca)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true", help="print full gasket.fusion.v1 JSON per run")
    args = ap.parse_args(argv)

    print(f"# certified agent run demo  (gasket {GASKET_VERSION}, eleata-verify {EVERIFY_VERSION})")
    print("# cost ⊕ risk ⊕ ε-interference — a CONDITIONAL, single-channel analysis, NOT a joint guarantee\n")

    cost_report = gasket_cost_report(REPO / "examples" / "workflows")
    base = _overlap_base()
    calibrator = _synthetic_calibrator()
    calib_digest = fusion.digest(json.loads(json.dumps(calibrator.__dict__, default=float)))
    spends = _spend_sample()
    print(f"# uncapped spend sample: n={len(spends)}  mean={sum(spends)/len(spends):.2f}  "
          f"max={max(spends):.2f}  (unit)")
    print(f"# verifier coverage c={COVERAGE}, SLA α={ALPHA} (δ={DELTA}), ε confidence 1−η={1-DELTA_EPS}\n")

    evidence = ("El Código Penal argentino, art. 79, reprime el homicidio simple con reclusión o "
                "prisión de ocho a veinticinco años.")
    claim = "El homicidio simple se reprime con prisión de ocho a veinticinco años."

    scenarios = [
        ("(A) NON-VACUOUS — loose cap (b=20 > max spend): 0 exceedances (k=0) ⇒ closed-form-verified ε "
         "⇒ small α inflation", 20.0, "certified-run-non-vacuous"),
        ("(B) VACUOUS — binding cap (b=3): many runs exceed ⇒ the bound saturates to 1.0 ⇒ SLA not "
         "preserved under the cap", 3.0, "certified-run-vacuous"),
    ]
    for label, cap, run_id in scenarios:
        print(f"================ {label} ================")
        bundle = certified_run(cost_report, base, calibrator, calib_digest, claim, evidence,
                               cap=cap, run_id=run_id)
        print(fusion.dumps(bundle) if args.json else fusion.pretty(bundle))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
