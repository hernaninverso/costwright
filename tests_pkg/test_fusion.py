"""Tests del bundler de fusión (spec 003): schema golden, validación estricta del contrato risk,
tamper-evidence, INVARIANTES DE HONESTIDAD (joint_guarantee siempre false, disclaimer presente, sin
booleano agregado de seguridad, sin "agente seguro"), y el CLI `costwright fuse` e2e."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from costwright import fusion  # noqa: E402

PY = sys.executable


def _report(*categories, vacuous=0):
    """A minimal valid costwright.v1 report with one unit per category given."""
    units, counts = [], {"certifiable": 0, "default_dependent": 0, "non_certifiable": 0,
                         "runaway": 0, "parse_error": 0}
    for i, c in enumerate(categories):
        counts[c] += 1
        units.append({"unit_id": f"u{i}", "file": f"f{i}.py", "span": {"line": i},
                      "framework": "langgraph", "category": c,
                      "bound": {"supersteps": 1000, "node_executions_ceiling": 1000,
                                "aggregation": "max", "provenance": "framework_default"},
                      "reasons": []})
    return {"schema": "costwright.v1", "units": units,
            "summary": {"total": len(units), **counts, "vacuous_default_bounds": vacuous},
            "signature": None}


def _vr(**over):
    d = {"verdict": "Supported", "calibrated_confidence": 0.97, "abstain": False,
         "abstain_reason": None, "sla_mode": "balanced", "sla_alpha": 0.05, "sla_certified": True,
         "evidence_cited": "evidence text", "score_outlier_warning": False, "raw_support_prob": 0.9,
         "sufficiency": None, "base_name": "stub", "domain": "demo"}
    d.update(over)
    return d


def _fuse(report=None, vr=None, **kw):
    kw.setdefault("run_id", "R")
    kw.setdefault("costwright_version", "0.1.0")
    kw.setdefault("verify_version", "0.1.0")
    return fusion.fuse(report or _report("certifiable"), vr or _vr(), **kw)


# --- cost_certificate -------------------------------------------------------------------------------
def test_cost_worst_category_conservative():
    # peor categoría presente (severidad runaway > non_certifiable > parse_error > default > certifiable)
    assert fusion.cost_certificate(_report("certifiable", "runaway"),
                                   costwright_version="0.1.0")["worst_category"] == "runaway"
    assert fusion.cost_certificate(_report("certifiable", "default_dependent"),
                                   costwright_version="0.1.0")["status"] == "default_dependent"
    assert fusion.cost_certificate(_report("certifiable"),
                                   costwright_version="0.1.0")["status"] == "certifiable"


def test_cost_no_units():
    c = fusion.cost_certificate(_report(), costwright_version="0.1.0")
    assert c["worst_category"] is None and c["status"] == "no_graph_units"


def test_cost_rejects_non_costwright_v1():
    for bad in ({"schema": "costwright.v2"}, {"units": [], "summary": {}}, [], "x"):
        try:
            fusion.cost_certificate(bad, costwright_version="0.1.0")
            assert False, f"should have rejected {bad!r}"
        except ValueError:
            pass


def test_cost_certified_unit_requires_a_valid_positive_integer_ceiling():
    # codex r96: cost_certificate validated only the CATEGORY — a unit with category 'certifiable' but a missing
    # / None / negative / zero / non-int node_executions_ceiling was accepted and reported status 'certifiable'
    # (false assurance). A certified-or-default unit must carry a positive-integer ceiling; fail-closed
    # categories legitimately carry no number.
    def _rep(ceil, cat="certifiable"):
        u = {"category": cat, "file": "x.py", "span": {"line": 1}, "framework": "langgraph", "reasons": []}
        if ceil is not _MISSING:
            u["bound"] = {"supersteps": ceil, "node_executions_ceiling": ceil,
                          "aggregation": "max", "provenance": "explicit"}
        return {"schema": "costwright.v1", "units": [u], "summary": {"total": 1}}
    for bad in (None, -5, 0, "big", True):
        try:
            fusion.cost_certificate(_rep(bad), costwright_version="0.1.0")
            assert False, f"must reject certifiable with ceiling={bad!r}"
        except ValueError:
            pass
    # a missing bound entirely is also rejected for a certified unit
    try:
        fusion.cost_certificate(_rep(_MISSING), costwright_version="0.1.0")
        assert False, "must reject certifiable with no bound"
    except ValueError:
        pass
    # a valid positive integer (incl. a huge one) is accepted; a fail-closed unit needs no number
    assert fusion.cost_certificate(_rep(10), costwright_version="0.1.0")["status"] == "certifiable"
    assert fusion.cost_certificate(_rep(10**9), costwright_version="0.1.0")["status"] == "certifiable"
    assert fusion.cost_certificate(_rep(None, cat="runaway"), costwright_version="0.1.0")["status"] == "runaway"


_MISSING = object()


def test_cost_rejects_unknown_or_malformed_category():
    # audit-3 (codex BLOCKER): una categoría desconocida NO debe colarse y degradar el status.
    _b = {"supersteps": 5, "node_executions_ceiling": 5, "aggregation": "max", "provenance": "explicit"}
    bad_reports = [
        {"schema": "costwright.v1", "units": [{"category": "certifiable", "bound": _b}, {"category": "runaway_typo"}],
         "summary": {"total": 2}},
        {"schema": "costwright.v1", "units": [{"category": "certifiable", "bound": _b}, "not-a-dict"],
         "summary": {"total": 2}},
        {"schema": "costwright.v1", "units": [{"no_category": True}], "summary": {"total": 1}},
    ]
    for rep in bad_reports:
        try:
            fusion.cost_certificate(rep, costwright_version="0.1.0")
            assert False, f"should reject {rep['units']}"
        except ValueError:
            pass


def test_cost_rejects_bad_workflow_digest():
    for bad in ("", 123, b"x"):
        try:
            fusion.cost_certificate(_report("certifiable"), costwright_version="0.1.0", workflow_digest=bad)
            assert False, f"should reject workflow_digest={bad!r}"
        except ValueError:
            pass


# --- risk_certificate: strict stdlib validation (council P0) ----------------------------------------
def test_risk_status_derivation():
    assert fusion.risk_certificate(_vr(), verify_version="0")["status"] == "answered"
    assert fusion.risk_certificate(_vr(abstain=True, abstain_reason="low_confidence"),
                                   verify_version="0")["status"] == "abstained"
    # uncertified domina sobre abstain (si no hay SGR, no hay garantía aunque "responda")
    assert fusion.risk_certificate(_vr(sla_certified=False), verify_version="0")["status"] == "uncertified"
    assert fusion.risk_certificate(_vr(sla_certified=False, abstain=True),
                                   verify_version="0")["status"] == "uncertified"


def test_risk_rejects_missing_required():
    bad = _vr(); del bad["sla_mode"]
    try:
        fusion.risk_certificate(bad, verify_version="0")
        assert False, "missing sla_mode must raise"
    except ValueError as e:
        assert "sla_mode" in str(e)


def test_risk_rejects_invalid_types_and_enums():
    cases = [_vr(abstain="yes"),               # bool field as str
             _vr(sla_mode="medium"),            # bad enum
             _vr(verdict="Universally Safe"),   # audit-3: arbitrary verdict label (anti-overclaim)
             _vr(verdict="supported"),          # wrong case ⇒ not in the closed enum
             _vr(calibrated_confidence=1.5),    # out of [0,1]
             _vr(calibrated_confidence=True),   # bool sneaking in as number
             _vr(sla_alpha=0.0),                # not in (0,1]
             _vr(sufficiency=float("inf")),     # audit-3: non-finite ⇒ invalid JSON
             _vr(sufficiency=float("nan")),     # audit-3: NaN
             _vr(evidence_cited=123)]           # str field as int
    for bad in cases:
        try:
            fusion.risk_certificate(bad, verify_version="0")
            assert False, f"should reject {bad}"
        except ValueError:
            pass


def test_risk_adversarial_inputs_raise_valueerror_not_typeerror():
    # audit-3 R2 (codex): valores no-hashables / ints gigantes deben dar ValueError limpio,
    # nunca TypeError ('unhashable') ni OverflowError (int→float en math.isfinite).
    cases = [_vr(verdict=[]), _vr(verdict={}), _vr(sla_mode=["strict"]),    # unhashable en `in set`
             _vr(sla_alpha=10 ** 1000), _vr(calibrated_confidence=10 ** 1000)]  # int gigante
    for bad in cases:
        try:
            fusion.risk_certificate(bad, verify_version="0")
            assert False, f"should reject {bad}"
        except ValueError:
            pass        # cualquier otra excepción (TypeError/OverflowError) propaga y falla el test


def test_cost_unhashable_category_raises_valueerror():
    rep = {"schema": "costwright.v1", "units": [{"category": ["certifiable"]}], "summary": {"total": 1}}
    try:
        fusion.cost_certificate(rep, costwright_version="0.1.0")
        assert False, "unhashable category must raise ValueError"
    except ValueError:
        pass


def test_fuse_huge_created_unix_no_crash():
    # int gigante no es finito-en-float pero ES un entero válido: no debe crashear (OverflowError)
    b = _fuse(created_unix=10 ** 1000)
    assert b["run"]["created_unix"] == 10 ** 1000
    assert b["run"]["fusion_digest"].startswith("sha256:")


def test_risk_accepts_all_known_verdicts():
    for v in ("Supported", "Refuted", "Not Enough Evidence", "Conflicting"):
        assert fusion.risk_certificate(_vr(verdict=v), verify_version="0")["verdict"] == v


def test_risk_rejects_bad_binding_strings():
    for kw in ({"calibrator_digest": ""}, {"calibrator_digest": 5}, {"claim": ""}, {"claim": 7}):
        try:
            fusion.risk_certificate(_vr(), verify_version="0", **kw)
            assert False, f"should reject {kw}"
        except ValueError:
            pass


def test_risk_tolerates_extra_fields():
    # contrato additive-only: campos nuevos desconocidos NO deben romper
    d = _vr(relevance=0.8, gated=False, brand_new_field={"a": 1})
    r = fusion.risk_certificate(d, verify_version="0")
    assert r["status"] == "answered"


def test_risk_binding_digests():
    r = fusion.risk_certificate(_vr(evidence_cited="abc"), verify_version="0", claim="hello")
    assert r["evidence_digest"] == fusion.digest_text("abc")
    assert r["claim_digest"] == fusion.digest_text("hello")
    assert fusion.risk_certificate(_vr(), verify_version="0")["claim_digest"] is None  # sin claim → null


# --- fuse: schema + honesty invariants --------------------------------------------------------------
def test_fuse_schema_keys():
    b = _fuse()
    assert b["schema"] == "costwright.fusion.v1"
    assert set(b) == {"schema", "run", "cost", "risk", "composition", "conditional_analyses", "signature"}
    assert set(b["run"]) == {"run_id", "created_unix", "fusion_digest"}
    assert b["signature"] is None
    assert b["conditional_analyses"] is None          # null unless the caller provides the ε-analysis (spec 004)
    assert b["cost"]["source"] == "costwright.v1" and b["risk"]["source"] == "eleata-verify.verify"
    assert "theorem" in b["cost"] and "guarantee" in b["risk"]
    assert b["cost"]["scope"] and b["risk"]["scope"]  # scopes temporales explícitos


def test_fuse_honesty_invariants():
    # PARA CUALQUIER input, el bundle NO se puede leer como garantía compuesta.
    for report, vr in [(_report("certifiable"), _vr()),
                       (_report("runaway"), _vr(abstain=True, abstain_reason="low_confidence")),
                       (_report("default_dependent"), _vr(sla_certified=False))]:
        b = _fuse(report, vr)
        comp = b["composition"]
        assert comp["joint_guarantee"] is False                 # SIEMPRE false
        assert comp["kind"] == "cartesian-product"
        assert isinstance(comp["disclaimer"], str) and len(comp["disclaimer"]) > 100
        assert "non_interference" in comp and comp["non_interference"]
        # NO existe ningún booleano agregado de "ambos pasaron / seguro"
        blob = fusion.canonical(b).lower()
        assert "both_independently_certified" not in blob
        assert "agent is safe" not in blob.replace("does not assert the agent is safe", "")
        # los dos status viven separados, nunca colapsados en uno
        assert b["cost"]["status"] and b["risk"]["status"]


def test_fuse_deterministic_and_tamper_evident():
    b1, b2 = _fuse(), _fuse()
    assert b1["run"]["fusion_digest"] == b2["run"]["fusion_digest"]          # determinista
    altered = _report("certifiable")
    altered["units"][0]["bound"]["supersteps"] = 999
    b3 = _fuse(altered, _vr())
    assert b3["cost"]["report_digest"] != b1["cost"]["report_digest"]        # report tampered
    assert b3["run"]["fusion_digest"] != b1["run"]["fusion_digest"]          # propaga al bundle


def test_fuse_fusion_digest_recomputable():
    # el fusion_digest debe re-computarse: bundle con fusion_digest=null → digest == el guardado
    b = _fuse()
    saved = b["run"]["fusion_digest"]
    b["run"]["fusion_digest"] = None
    assert fusion.digest(b) == saved


def test_fuse_rejects_bad_run_id_and_created_unix():
    bad_kw = [{"run_id": ""}, {"run_id": "   "}, {"run_id": 5},          # audit-3: whitespace run_id
              {"created_unix": "today"}, {"created_unix": -1},           # audit-3: negative epoch
              {"created_unix": float("nan")}, {"created_unix": float("inf")}]  # audit-3: non-finite
    for kw in bad_kw:
        try:
            _fuse(**kw)
            assert False, f"should reject {kw}"
        except ValueError:
            pass


def test_canonical_rejects_non_finite():
    # audit-3 (codex BLOCKER): NaN/Infinity no son JSON válido — canonical/digest deben fallar, no emitir.
    for obj in ({"x": float("nan")}, {"x": float("inf")}):
        try:
            fusion.canonical(obj)
            assert False, f"canonical must reject {obj}"
        except ValueError:
            pass


def test_canonical_rejects_ambiguous_keys_no_digest_collision():
    # codex r92: json.dumps(sort_keys=True) COERCES a non-string dict key to a string, so {1:"x"} and {"1":"x"}
    # canonicalize identically and COLLIDE → two structurally-distinct bundles share a digest = false tamper-
    # evidence. canonical now fails closed on a non-string key (and on a tuple, which serializes as a list).
    for bad in ({"a": {1: "x"}}, {"a": {True: "x"}}, {"a": {None: "x"}}, {"a": (1, 2)}, {1: "top"}):
        try:
            fusion.canonical(bad)
            assert False, f"canonical must reject {bad}"
        except ValueError:
            pass
    # the colliding pair now produces DISTINCT outcomes: the str-key form canonicalizes, the int-key form raises
    assert fusion.canonical({"a": {"1": "x"}}) == '{"a":{"1":"x"}}'
    try:
        fusion.digest({"a": {1: "x"}})
        assert False, "digest of an int-keyed dict must reject, not collide"
    except ValueError:
        pass


def test_pretty_surfaces_disclaimer_and_two_statuses():
    out = fusion.pretty(_fuse())
    assert "COST" in out and "RISK" in out
    assert "joint_guarantee=False" in out
    assert "CARTESIAN PRODUCT" in out and "NOT a composed guarantee" in out


# --- CLI `costwright fuse` e2e ---------------------------------------------------------------------------
def _run_fuse(*args):
    return subprocess.run([PY, "-m", "costwright.cli", "fuse", *args], capture_output=True, text=True,
                          env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})


def test_cli_fuse_golden_and_exit0():
    with tempfile.TemporaryDirectory() as td:
        cp, rp = Path(td) / "cost.json", Path(td) / "risk.json"
        cp.write_text(json.dumps(_report("certifiable")))
        rp.write_text(json.dumps(_vr()))
        r = _run_fuse("--cost", str(cp), "--risk", str(rp), "--run-id", "R1",
                      "--verify-version", "0.1.0", "--json")
        assert r.returncode == 0, (r.returncode, r.stderr)
        b = json.loads(r.stdout)
        assert b["schema"] == "costwright.fusion.v1"
        assert b["composition"]["joint_guarantee"] is False
        assert b["cost"]["status"] == "certifiable" and b["risk"]["status"] == "answered"
        assert b["run"]["run_id"] == "R1"


def test_cli_fuse_malformed_risk_exit2():
    with tempfile.TemporaryDirectory() as td:
        cp, rp = Path(td) / "cost.json", Path(td) / "bad.json"
        cp.write_text(json.dumps(_report("certifiable")))
        bad = _vr(); del bad["sla_certified"]
        rp.write_text(json.dumps(bad))
        r = _run_fuse("--cost", str(cp), "--risk", str(rp), "--run-id", "R1")
        assert r.returncode == 2 and "invalid certificate input" in r.stderr


def test_cli_fuse_bad_path_exit2():
    r = _run_fuse("--cost", "/nope/x.json", "--risk", "/nope/y.json", "--run-id", "R1")
    assert r.returncode == 2


def test_fusion_is_zero_dep():
    # importar costwright.fusion NO debe arrastrar eleata_verify ni numpy (core zero-dep preservado)
    r = subprocess.run(
        [PY, "-c", "import costwright.fusion, sys; "
                   "assert 'eleata_verify' not in sys.modules, 'fusion imported eleata_verify'; "
                   "assert 'numpy' not in sys.modules, 'fusion imported numpy'; print('ok')"],
        capture_output=True, text=True, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})
    assert r.returncode == 0 and "ok" in r.stdout, (r.returncode, r.stdout, r.stderr)


def test_cli_fuse_workflow_binding():
    with tempfile.TemporaryDirectory() as td:
        cp, rp = Path(td) / "cost.json", Path(td) / "risk.json"
        cp.write_text(json.dumps(_report("certifiable")))
        rp.write_text(json.dumps(_vr()))
        wf = Path(td) / "wf.py"; wf.write_bytes(b"x = 1\n")
        r = _run_fuse("--cost", str(cp), "--risk", str(rp), "--run-id", "R1", "--workflow", str(wf), "--json")
        b = json.loads(r.stdout)
        assert b["cost"]["workflow_digest"] == fusion.digest_bytes(b"x = 1\n")


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    bad = 0
    for fn in fns:
        try:
            fn(); print(f"  ✓ {fn.__name__}")
        except AssertionError as e:
            bad += 1; print(f"  ✗ {fn.__name__}: {str(e)[:160]}")
    print(f"{len(fns)-bad}/{len(fns)} PASS")
    sys.exit(1 if bad else 0)
