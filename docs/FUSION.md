# gasket fusion — the cartesian product of certificates (experimental)

> **Experimental** (spec `003-gasket-fusion-cartesian-cert`, Exp #4 of the eleata-verify roadmap).
> A `gasket.fusion.v1` bundle pairs gasket's **cost** certificate with an [eleata-verify](https://github.com/hernaninverso/eleata-verify)
> **risk** certificate into one audit record. It is their **cartesian product**, **NOT a composed
> guarantee** — see [NON-INTERFERENCE.md](./NON-INTERFERENCE.md).

## Why bundle them

A "trustworthy agent run" answers two orthogonal questions:

| Question | Certificate | Kind |
|---|---|---|
| *Will this blow my budget?* | **cost** — `gasket check` (gasket.v1), backed by the Lean cost-soundness theorem | static, ahead-of-time, **every trace** |
| *Can I trust this output, or send it to a human?* | **risk** — `eleata_verify.verify()` (SGR selective-risk SLA) | per-output, **i.i.d. population** SLA, domain-bounded |

`gasket fuse` records both in one tamper-evident artifact per run. That is operationally useful as an
**audit record** — without claiming the two compose into a single "safe" verdict.

## Honesty boundary (what a bundle does NOT claim)

Every bundle carries `composition.joint_guarantee = false`, a disclaimer, and a non-interference
caveat. The bundle:

- does **not** assert the agent is "safe";
- has **no** aggregate "both passed" boolean (it would be consumed as a green badge — see council 003);
- does **not** multiply/combine the two confidence levels, nor assert statistical independence;
- does **not** claim a bounded budget preserves the risk SLA (a budget cap can shift the output
  distribution off the risk calibration domain — that interaction is the whole content of the
  [non-interference theorem](./NON-INTERFERENCE.md), which is **unproven / future work**).

Digests in a bundle are **tamper-evidence, not proof of authorship** — authenticity is the job of the
signed certification layer (`signature` is reserved `null` in the OSS bundle, exactly as in gasket.v1).

## Architecture (zero-dep core preserved)

```
gasket check . --json ─────────► cost.json   (gasket.v1)
eleata_verify.verify(...) ─────► risk.json   (VerifyResult.to_dict())   ← only YOUR code imports eleata-verify
                                      │
            gasket fuse --cost cost.json --risk risk.json --run-id <id> ─► gasket.fusion.v1
                                      │
                          src/gasket/fusion.py  — PURE STDLIB; never imports eleata_verify
```

`gasket.fusion` only knows the *shape* of the two JSON dicts (eleata-verify's frozen, additive-only
[contract](https://github.com/hernaninverso/eleata-verify/blob/main/docs/API-CONTRACT.md)). Adding
fusion keeps gasket's **zero runtime dependencies** guarantee — verified by the test that the bundler
imports cleanly with nothing but the stdlib. The bundler **strictly validates** the risk dict (required
keys, types, enums) and tolerates only *extra* fields, so an upstream breaking change surfaces as an
error instead of corrupting a bundle.

## CLI

```bash
gasket check . --json > cost.json          # your existing CI step
# ... your eleata-verify step writes risk.json (VerifyResult.to_dict()) ...
gasket fuse --cost cost.json --risk risk.json --run-id "$RUN_ID" \
            --verify-version 0.1.0 \
            --workflow .          # optional: digest the analyzed workflow (anti-substitution binding)
            # --claim-file claim.txt --calibrator-digest sha256:... --created-unix 1750000000 --json
```

- exit `0` = the bundle was produced; exit `2` = infrastructure error (missing file, bad JSON, invalid
  certificate input). There is no `--fail-on`: **a bundle is an audit record, not a CI policy** — gate
  on the two component checks (`gasket check --fail-on …` and your verify step), not on the fusion.

## Schema `gasket.fusion.v1` (frozen here; additive-only in v2)

```jsonc
{
  "schema": "gasket.fusion.v1",
  "run":  { "run_id": "...", "created_unix": 1750000000 | null,
            "fusion_digest": "sha256:..." },            // over the bundle with fusion_digest+signature null
  "cost": { "source": "gasket.v1", "gasket_version": "0.1.0",
            "report_digest": "sha256:...",              // canonical-JSON digest of the gasket.v1 report
            "workflow_digest": "sha256:..." | null,     // binding to the analyzed artifact
            "scope": "ahead-of-time; static; every trace",
            "summary": { ... }, "worst_category": "...", "status": "...",
            "theorem": { "name","mechanized","doi","scope" } },
  "risk": { "source": "eleata-verify.verify", "verify_version": "0.1.0",
            "result_digest": "sha256:...", "evidence_digest": "sha256:...",
            "claim_digest": "sha256:..." | null, "calibrator_digest": "..." | null,
            "scope": "per-output; i.i.d. population SLA on the calibration domain",
            "verdict","calibrated_confidence","abstain","abstain_reason","sla_mode","sla_alpha",
            "sla_certified","score_outlier_warning","domain","evidence_cited",
            "status": "answered" | "abstained" | "uncertified",
            "guarantee": { "name","scope" } },
  "composition": { "kind": "cartesian-product", "joint_guarantee": false,
                   "disclaimer": "...", "non_interference": "... see docs/NON-INTERFERENCE.md" },
  "signature": null                                     // reserved: the signed certification layer fills it
}
```

- `cost.status` = the **worst** unit category (severity `runaway > non_certifiable > parse_error >
  default_dependent > certifiable`), or `no_graph_units`.
- `risk.status` = `uncertified` (calibrator carries no SGR guarantee), else `abstained`, else `answered`.

## Demo

`examples/fusion_demo.py` runs the full pipeline offline and deterministically (`demonstration_only`):
`gasket check` over a certifiable LangGraph fixture → a **real** `eleata_verify.verify()` call over a
synthetic lexical-overlap base → `fuse()`, printing both an **answered** and an **abstained** bundle.

```bash
pip install -e ~/eleata-verify numpy      # eleata-verify is the pinned black-box dep (private repo)
python examples/fusion_demo.py
```

The default base is a trivial stub — it demonstrates the **fusion mechanics**, not a production risk
model. An **opt-in real-NLI** variant wires the Toga legal-AR NLI model:

```bash
python examples/fusion_demo.py --base nli --model-dir ~/toga/training/nli_legal_ar_v1   # needs torch
```

(With a real base you must **recalibrate** the eleata-verify `Calibrator` on in-domain `(score,
correct)` pairs — the demo's synthetic calibrator is valid only for the synthetic base.)
