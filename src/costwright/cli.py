"""costwright CLI — `costwright check` y `costwright caps`.

Exit codes (council 002 P0-1):
  0 = el tool corrió (hallazgos = warnings, salvo política)
  1 = la política --fail-on se violó
  2 = error de infraestructura (path inválido, crash) — nunca severidad de hallazgo
"""
import argparse
import json
import sys
from pathlib import Path

from costwright import __version__
from costwright import caps as caps_mod
from costwright import report as report_mod
from costwright.extract import extract_unit
from costwright.mapper import map_unit
import ast as _ast

EXCLUDE_DIRS = {".venv", "venv", "node_modules", "site-packages", ".git", "__pycache__"}


def _find_units(root: Path, max_files: int):
    """Detecta graph units (constructores LangGraph/CrewAI/AgentsSDK) en el árbol."""
    units = []
    n = 0
    for py in sorted(root.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        # audit-3 (deepseek P0): NO seguir symlinks — un repo hostil podría apuntar
        # fuera del árbol escaneado (path traversal del scanner)
        if py.is_symlink() or any(p.is_symlink() for p in py.parents
                                  if root in p.parents or p == root):
            continue
        n += 1
        if n > max_files:
            break
        try:
            src = py.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        # precheck laxo (audit-3: "Crew (" con espacio se perdía con "Crew("). "Runner" (no "Runner.run")
        # porque un `from agents import Runner as R` + `R.run(...)` no contiene la cadena "Runner.run" pero el
        # import sí trae "Runner" (codex r84) — el AST resuelve el alias del receptor abajo.
        if not any(k in src for k in ("StateGraph", "Crew", "Runner")):
            continue
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            units.append({"file": py, "kind": "unknown", "line": 0, "syntax_error": True})
            continue
        # resolve `from langgraph.graph import StateGraph as SG` so an aliased framework constructor is still
        # discovered as a unit (codex/Cursor r81) — otherwise the graph is silently dropped from the report.
        alias = {a.asname: a.name for nd in _ast.walk(tree) if isinstance(nd, _ast.ImportFrom)
                 for a in nd.names if a.asname}
        # a SUBCLASS of a framework base inherits its behaviour — `class MyGraph(StateGraph)`,
        # `class MyCrew(Crew)`, `class UnboundedRunner(Runner)` — but the constructor / receiver carries the
        # SUBCLASS name, not the base, so by-name detection silently DROPS the unit (codex r85 Runner; Cursor
        # r92 StateGraph). Collect subclass names per base (fixpoint over TRANSITIVE subclasses, base resolved
        # through the import alias) so their constructor / .run* calls are still discovered.
        _BASES = ("StateGraph", "Crew", "Runner")
        subclasses = {b: set() for b in _BASES}
        _classdefs = [nd for nd in _ast.walk(tree) if isinstance(nd, _ast.ClassDef)]
        _changed = True
        while _changed:
            _changed = False
            for nd in _classdefs:
                for base in nd.bases:
                    bn = (alias.get(base.id, base.id) if isinstance(base, _ast.Name)
                          else base.attr if isinstance(base, _ast.Attribute) else None)
                    for b in _BASES:
                        if (bn == b or bn in subclasses[b]) and nd.name not in subclasses[b]:
                            subclasses[b].add(nd.name)
                            _changed = True
        runner_subclasses = subclasses["Runner"]
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            f = node.func
            # resolve the import alias of the RECEIVER, not just a bare Name: `from agents import Runner as R`
            # then `R.run(...)` has receiver Name `R` → resolve to `Runner` BEFORE composing `Runner.run`
            # (codex r84). The old code resolved the full dotted string `R.run`, which never matched the
            # alias map (keyed on `R`), so an aliased Runner call was silently dropped.
            if isinstance(f, _ast.Name):
                nm = alias.get(f.id, f.id)
            elif isinstance(f, _ast.Attribute) and isinstance(f.value, _ast.Name):
                recv = alias.get(f.value.id, f.value.id)
                if recv in runner_subclasses:    # UnboundedRunner.run_sync ⇒ Runner.run_sync (codex r85)
                    recv = "Runner"
                nm = f"{recv}.{f.attr}"
            elif isinstance(f, _ast.Attribute):
                nm = f.attr
            else:
                nm = ""
            kind = None
            if nm == "StateGraph" or nm in subclasses["StateGraph"]:
                kind = "langgraph"
            elif nm == "Crew" or nm in subclasses["Crew"]:
                kind = "crewai"
            elif nm in ("Runner.run", "Runner.run_sync", "Runner.run_streamed"):
                kind = "agents_sdk"
            if kind:
                units.append({"file": py, "kind": kind, "line": node.lineno})
    return units


def cmd_check(args) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"costwright: path not found: {root}", file=sys.stderr)
        return 2
    try:
        found = _find_units(root, args.max_files)
        mapped = []
        for u in found:
            rel = str(u["file"].relative_to(root))
            if u.get("syntax_error"):
                mapped.append({"category": "extractor-failure", "reason": "syntax",
                               "kind": u["kind"], "rel_path": rel, "line": 0})
                continue
            meta = {"unit_id": rel, "file": u["file"].name, "kind": u["kind"]}
            ex = extract_unit(u["file"].parent, meta)
            r = map_unit(ex, meta)
            r["rel_path"] = rel
            r["line"] = u["line"]
            mapped.append(r)
        rep = report_mod.to_v1(mapped)
        if args.json:
            print(report_mod.dumps(rep))
        else:
            if not rep["units"]:
                print("costwright: no graph units found")
                return 0
            print(report_mod.pretty(rep, verbose=args.verbose))
        # política opt-in (council 002 P0-1)
        s = rep["summary"]
        # "exit 1 on findings of this severity OR WORSE", MONOTONIC and complete (codex r72): a unit costwright
        # could not analyze (`parse_error`, incl. extractor-failure / nonpositive-bound) is "couldn't certify"
        # and must NOT silently pass; and a `non_certifiable` unit is WORSE than `default_dependent`, so it must
        # also trip the stricter default-dependent threshold. reject ⊆ non-certifiable ⊆ default-dependent.
        not_certified = s["runaway"] > 0 or s["non_certifiable"] > 0 or s["parse_error"] > 0
        viol = {"reject": s["runaway"] > 0,
                "non-certifiable": not_certified,
                "default-dependent": not_certified or s["default_dependent"] > 0}
        if args.fail_on and viol.get(args.fail_on, False):
            print(f"costwright: policy --fail-on {args.fail_on} violated", file=sys.stderr)
            return 1
        return 0
    except Exception as e:                                    # noqa: BLE001
        print(f"costwright: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def cmd_caps(args) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"costwright: path not found: {root}", file=sys.stderr)
        return 2
    try:
        per_file, scanned = caps_mod.scan_path(root, args.max_files)
        if args.json:
            out = {"schema": "costwright.caps.v1", "files_scanned": scanned, "findings": [
                {**f, "file": str(p.relative_to(root))}
                for p, (fs, _) in sorted(per_file.items()) for f in fs]}
            print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True))
        else:
            total = sum(len(fs) for fs, _ in per_file.values())
            if not total:
                # NOT "all capped" — a static by-name scan cannot see every construction (a non-literal
                # `getattr(m, var)`, a factory function, a subclass). State exactly what was checked (Cursor r83).
                print(f"costwright caps: no uncapped LLM constructors detected by name "
                      f"({scanned} files scanned). Reflective/dynamic construction is not covered.")
                return 0
            for p, (fs, _) in sorted(per_file.items()):
                rel = p.relative_to(root)
                for f in fs:
                    if f["kind"] == "missing":
                        print(f"  ✗ {rel}:{f['line']}  {f['constructor']}(...) sin cap "
                              f"→ agregar {f['suggest_kwarg']}=<N>"
                              + (f"   [{f['note']}]" if f.get("note") else ""))
                    else:
                        print(f"  ▲ {rel}:{f['line']}  {f['constructor'] or '(file)'}: {f['why']}")
            print(f"\n  {total} finding(s) in {len(per_file)} file(s) "
                  f"({scanned} scanned). Use --patch to emit a unified diff.")
        if args.patch:
            if args.cap < 1:
                # a cap of 0/negative is not an effective token bound — the patch would insert an inert kwarg
                # that costwright itself flags `ineffective` (codex r75). Refuse instead of suggesting it.
                print(f"costwright: --cap must be a positive integer (got {args.cap})", file=sys.stderr)
                return 2
            chunks = []
            for p, (fs, src) in sorted(per_file.items()):
                d = caps_mod.make_patch(p.relative_to(root), src, fs, args.cap)
                if d:
                    chunks.append(d)
            patch = "".join(chunks)
            if args.patch == "-":
                sys.stdout.write(patch)
            else:
                Path(args.patch).write_text(patch)
                print(f"  patch written to {args.patch} (apply with: git apply {args.patch})")
        return 0
    except Exception as e:                                    # noqa: BLE001
        print(f"costwright: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def _load_json(path: str):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _workflow_digest(path: Path) -> str:
    """Bind the bundle to the analyzed artifact (anti-substitution). Hash the EXACT file bytes (not
    decoded text — `errors='ignore'` could let two byte-distinct files collide). A single file →
    digest its bytes; a directory → a deterministic manifest {rel_path: sha256(bytes)} over the *.py."""
    from costwright import fusion
    if path.is_dir():
        manifest = {}
        for py in sorted(path.rglob("*.py")):
            if any(part in EXCLUDE_DIRS for part in py.parts):
                continue
            manifest[str(py.relative_to(path))] = fusion.digest_bytes(py.read_bytes())
        return fusion.digest(manifest)
    return fusion.digest_bytes(path.read_bytes())


def cmd_fuse(args) -> int:
    from costwright import __version__, fusion
    try:
        cost = _load_json(args.cost)
        risk = _load_json(args.risk)
    except (OSError, ValueError) as e:   # ValueError covers json.JSONDecodeError
        print(f"costwright: cannot read input JSON: {type(e).__name__}: {e}", file=sys.stderr)
        return 2
    claim = None
    if args.claim_file:
        try:
            claim = Path(args.claim_file).read_text(encoding="utf-8")
        except OSError as e:
            print(f"costwright: cannot read --claim-file: {e}", file=sys.stderr)
            return 2
    wf_digest = None
    if args.workflow:
        try:
            wf_digest = _workflow_digest(Path(args.workflow))
        except OSError as e:
            print(f"costwright: cannot read --workflow: {e}", file=sys.stderr)
            return 2
    try:
        bundle = fusion.fuse(cost, risk, run_id=args.run_id,
                             costwright_version=args.costwright_version or __version__,
                             verify_version=args.verify_version,
                             created_unix=args.created_unix,
                             workflow_digest=wf_digest,
                             calibrator_digest=args.calibrator_digest, claim=claim)
    except ValueError as e:
        print(f"costwright: invalid certificate input: {e}", file=sys.stderr)
        return 2
    print(fusion.dumps(bundle) if args.json else fusion.pretty(bundle))
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="costwright",
        description="Static budget certificates for LLM-agent workflows "
                    "(LangGraph / CrewAI / OpenAI Agents SDK). Never executes your code.")
    p.add_argument("--version", action="version", version=f"costwright {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="map workflows to the typed-budget calculus and report bounds")
    c.add_argument("path", nargs="?", default=".")
    c.add_argument("--json", action="store_true", help="emit costwright.v1 JSON")
    c.add_argument("--verbose", action="store_true", help="also list certifiable units")
    c.add_argument("--fail-on", choices=["reject", "default-dependent", "non-certifiable"],
                   help="severity threshold: exit 1 on findings of this severity OR WORSE "
                        "(reject ⊂ default-dependent ⊂ non-certifiable). Default: never fail")
    c.add_argument("--max-files", type=int, default=5000)
    c.set_defaults(fn=cmd_check)

    k = sub.add_parser("caps", help="find LLM constructors without a token cap; suggest the right kwarg per provider")
    k.add_argument("path", nargs="?", default=".")
    k.add_argument("--json", action="store_true")
    k.add_argument("--patch", metavar="FILE", help="write a unified diff adding caps ('-' = stdout); NEVER edits files")
    k.add_argument("--cap", type=int, default=1024, help="cap value used in --patch (default 1024)")
    k.add_argument("--max-files", type=int, default=5000)
    k.set_defaults(fn=cmd_caps)

    from costwright.pack import cmd_pack
    pk = sub.add_parser("pack", help="build a deterministic .py-only tarball for server-side certification")
    pk.add_argument("path", nargs="?", default=".")
    pk.add_argument("-o", "--output", default="costwright-artifact.tgz")
    pk.set_defaults(fn=cmd_pack)

    fz = sub.add_parser("fuse", help="bundle a costwright.v1 cost cert + an eleata-verify risk cert into a "
                                     "costwright.fusion.v1 audit record (the cartesian product — NOT a joint guarantee)")
    fz.add_argument("--cost", required=True, metavar="FILE", help="costwright.v1 JSON (from `costwright check --json`)")
    fz.add_argument("--risk", required=True, metavar="FILE", help="eleata-verify VerifyResult.to_dict() JSON")
    fz.add_argument("--run-id", required=True, help="binds both certificates to the same run")
    fz.add_argument("--costwright-version", default=None, help="costwright that produced --cost (default: this costwright)")
    fz.add_argument("--verify-version", default="unknown", help="pinned eleata-verify version that produced --risk")
    fz.add_argument("--workflow", metavar="PATH", help="file/dir of the analyzed workflow → workflow_digest (binding)")
    fz.add_argument("--claim-file", metavar="FILE", help="the verified claim text → claim_digest (binding)")
    fz.add_argument("--calibrator-digest", default=None, help="digest/id of the calibrator used (binding)")
    fz.add_argument("--created-unix", type=int, default=None, help="caller-stamped run timestamp (optional)")
    fz.add_argument("--json", action="store_true", help="emit costwright.fusion.v1 JSON")
    fz.set_defaults(fn=cmd_fuse)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
