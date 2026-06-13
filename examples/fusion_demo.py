#!/usr/bin/env python3
"""End-to-end demo of the gasket × eleata-verify CARTESIAN PRODUCT of certificates (spec 003, Exp #4).

  demonstration_only — this demo proves the FUSION MECHANICS (schema, binding, abstention surfacing),
  NOT the utility of any production risk model or a production SLA. The default base verifier is a
  trivial deterministic lexical-overlap stub: it stands in for "some output-checker" so the demo runs
  offline with no GPU/torch. eleata-verify is base-agnostic; an opt-in real-NLI variant is below.

What it does, end to end:
  1. COST cert  — runs `gasket check examples/workflows --json` (a LangGraph graph with an explicit
     recursion_limit ⇒ `certifiable`), backed by the Lean cost-soundness theorem.
  2. RISK cert  — fits a deterministic eleata-verify Calibrator (synthetic data, fixed seed) and calls
     the REAL `eleata_verify.verify()` (consumed as a BLACK BOX) on two outputs:
        (a) a claim well-supported by the evidence  ⇒ status "answered";
        (b) a claim poorly supported by the evidence ⇒ status "abstained" (routed to human review).
  3. FUSE       — bundles each (cost, risk) pair into a `gasket.fusion.v1` audit record and prints it.

eleata-verify is a PINNED BLACK-BOX dependency (window A owns the contract; additive-only):
    pip install -e ~/eleata-verify        # local (repo is private)
    # or, pinned:  pip install "eleata-verify @ git+https://github.com/hernaninverso/eleata-verify.git@aa411e9"
    pip install numpy
See examples/requirements.txt.

Run:   python examples/fusion_demo.py
Opt-in real NLI (needs torch + the Toga legal-AR NLI model dir):
       python examples/fusion_demo.py --base nli --model-dir ~/toga/training/nli_legal_ar_v1
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
sys.path.insert(0, str(SRC))          # so `import gasket` works when run from the repo (uninstalled)

from gasket import __version__ as GASKET_VERSION       # noqa: E402
from gasket import fusion                               # noqa: E402

try:
    from eleata_verify import CallableVerifier, RawVerdict, fit, verify
    from eleata_verify import __version__ as EVERIFY_VERSION
except ImportError as e:                                # pragma: no cover
    sys.exit("eleata-verify not installed — see the header / examples/requirements.txt: "
             f"pip install -e ~/eleata-verify numpy   ({e})")


# --- COST certificate: run gasket check over the demo workflow --------------------------------------
def gasket_cost_report(path: Path) -> dict:
    env = {**os.environ, "PYTHONPATH": str(SRC)}
    r = subprocess.run([sys.executable, "-m", "gasket.cli", "check", str(path), "--json"],
                       capture_output=True, text=True, env=env)
    if r.returncode == 2:
        sys.exit(f"gasket check failed (infra): {r.stderr}")
    return json.loads(r.stdout)


# --- RISK base verifier: a deterministic lexical-overlap stub (demonstration_only) ------------------
def _overlap_base():
    def fn(claim: str, evidence: str) -> RawVerdict:
        cw = re.findall(r"\w+", claim.lower())
        ew = set(re.findall(r"\w+", evidence.lower()))
        if not cw:
            return RawVerdict("Not Enough Evidence", 0.5)
        j = sum(1 for w in cw if w in ew) / len(cw)     # fraction of claim words found in evidence
        if j >= 0.5:                                     # supported: confidence rises with overlap
            return RawVerdict("Supported", min(0.99, 0.55 + 0.44 * j))
        # not enough overlap: low confidence in the (NEI) call ⇒ the calibrator will abstain
        return RawVerdict("Not Enough Evidence", round(0.55 + 0.30 * j, 4))
    return CallableVerifier(fn, name="lexical-overlap-stub(demonstration_only)")


def _synthetic_calibrator():
    """Deterministic, certified Calibrator over synthetic (raw, correct) data on the [0.5,0.99] scale
    the overlap stub emits. Clean monotone signal ⇒ SGR certifies a threshold (certified=True)."""
    import numpy as np
    rng = np.random.default_rng(0)
    raw = rng.uniform(0.5, 0.99, size=800)
    p_correct = np.clip(1.35 * (raw - 0.5) / 0.49, 0.0, 1.0)     # high raw ⇒ almost always correct
    correct = (rng.random(800) < p_correct).astype(int)
    return fit(raw, correct, domain="demo-synthetic", seed=0)


def build_base(args):
    if args.base == "nli":
        if not args.model_dir:
            sys.exit("--base nli requires --model-dir <toga nli_legal_ar_v1 path>")
        from eleata_verify import toga_nli_verifier      # lazy: pulls torch/transformers only here
        # NOTE: with the real NLI base, the synthetic calibrator above is NOT valid for its scores;
        # a real deployment recalibrates on in-domain (raw, correct) pairs. This branch is a wiring
        # demo only (see docs/FUSION.md "opt-in real NLI").
        return toga_nli_verifier(args.model_dir), "nli"
    return _overlap_base(), "synthetic"


# --- one fused bundle for a (claim, evidence) output ------------------------------------------------
def fuse_one(cost_report, base, calibrator, claim, evidence, run_id, calib_digest):
    vr = verify(claim, evidence, base, calibrator, mode="balanced")
    return fusion.fuse(
        cost_report, vr.to_dict(), run_id=run_id,
        gasket_version=GASKET_VERSION, verify_version=EVERIFY_VERSION,
        workflow_digest=None, calibrator_digest=calib_digest, claim=claim,
    )


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", choices=["synthetic", "nli"], default="synthetic",
                    help="synthetic = offline lexical-overlap stub (default); nli = real Toga legal-AR NLI (opt-in)")
    ap.add_argument("--model-dir", default=None, help="NLI model dir (required with --base nli)")
    ap.add_argument("--json", action="store_true", help="print full gasket.fusion.v1 JSON for each output")
    args = ap.parse_args(argv)

    print(f"# gasket × eleata-verify fusion demo  (gasket {GASKET_VERSION}, eleata-verify {EVERIFY_VERSION})")
    print(f"# base verifier: {args.base}  —  demonstration_only\n")

    cost_report = gasket_cost_report(REPO / "examples" / "workflows")
    base, _ = build_base(args)
    calibrator = _synthetic_calibrator() if args.base == "synthetic" else _synthetic_calibrator()
    calib_digest = fusion.digest(json.loads(json.dumps(calibrator.__dict__, default=float)))
    print(f"# calibrator: domain={calibrator.domain} certified={calibrator.certified} "
          f"tau_balanced={round(calibrator.tau_balanced, 4)}\n")

    evidence = ("El Código Penal argentino, art. 79, reprime el homicidio simple con reclusión o "
                "prisión de ocho a veinticinco años.")
    outputs = [
        ("answered (claim supported by the evidence)",
         "El homicidio simple se reprime con prisión de ocho a veinticinco años.", "run-demo-answered"),
        ("abstained (claim NOT supported by the evidence → routed to human)",
         "La sociedad anónima requiere dos socios y un capital mínimo.", "run-demo-abstained"),
    ]

    for label, claim, run_id in outputs:
        print(f"================ {label} ================")
        bundle = fuse_one(cost_report, base, calibrator, claim, evidence, run_id, calib_digest)
        print(fusion.dumps(bundle) if args.json else fusion.pretty(bundle))
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
