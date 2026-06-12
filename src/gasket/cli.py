"""gasket CLI — `gasket check` y `gasket caps`.

Exit codes (council 002 P0-1):
  0 = el tool corrió (hallazgos = warnings, salvo política)
  1 = la política --fail-on se violó
  2 = error de infraestructura (path inválido, crash) — nunca severidad de hallazgo
"""
import argparse
import json
import sys
from pathlib import Path

from gasket import __version__
from gasket import caps as caps_mod
from gasket import report as report_mod
from gasket.extract import extract_unit
from gasket.mapper import map_unit
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
        # precheck laxo (audit-3: "Crew (" con espacio se perdía con "Crew(")
        if not any(k in src for k in ("StateGraph", "Crew", "Runner.run")):
            continue
        try:
            tree = _ast.parse(src)
        except SyntaxError:
            units.append({"file": py, "kind": "unknown", "line": 0, "syntax_error": True})
            continue
        for node in _ast.walk(tree):
            if not isinstance(node, _ast.Call):
                continue
            f = node.func
            nm = f.id if isinstance(f, _ast.Name) else (
                f"{f.value.id}.{f.attr}" if isinstance(f, _ast.Attribute)
                and isinstance(f.value, _ast.Name) else
                (f.attr if isinstance(f, _ast.Attribute) else ""))
            kind = None
            if nm == "StateGraph":
                kind = "langgraph"
            elif nm == "Crew":
                kind = "crewai"
            elif nm in ("Runner.run", "Runner.run_sync", "Runner.run_streamed"):
                kind = "agents_sdk"
            if kind:
                units.append({"file": py, "kind": kind, "line": node.lineno})
    return units


def cmd_check(args) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"gasket: path not found: {root}", file=sys.stderr)
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
                print("gasket: no graph units found")
                return 0
            print(report_mod.pretty(rep, verbose=args.verbose))
        # política opt-in (council 002 P0-1)
        s = rep["summary"]
        viol = {"reject": s["runaway"] > 0,
                "default-dependent": s["runaway"] > 0 or s["default_dependent"] > 0,
                "non-certifiable": (s["runaway"] > 0 or s["default_dependent"] > 0
                                    or s["non_certifiable"] > 0)}
        if args.fail_on and viol.get(args.fail_on, False):
            print(f"gasket: policy --fail-on {args.fail_on} violated", file=sys.stderr)
            return 1
        return 0
    except Exception as e:                                    # noqa: BLE001
        print(f"gasket: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def cmd_caps(args) -> int:
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"gasket: path not found: {root}", file=sys.stderr)
        return 2
    try:
        per_file, scanned = caps_mod.scan_path(root, args.max_files)
        if args.json:
            out = {"schema": "gasket.caps.v1", "files_scanned": scanned, "findings": [
                {**f, "file": str(p.relative_to(root))}
                for p, (fs, _) in sorted(per_file.items()) for f in fs]}
            print(json.dumps(out, indent=1, ensure_ascii=False, sort_keys=True))
        else:
            total = sum(len(fs) for fs, _ in per_file.values())
            if not total:
                print(f"gasket caps: all LLM constructors capped ({scanned} files scanned)")
                return 0
            for p, (fs, _) in sorted(per_file.items()):
                rel = p.relative_to(root)
                for f in fs:
                    if f["kind"] == "missing":
                        print(f"  ✗ {rel}:{f['line']}  {f['constructor']}(...) sin cap "
                              f"→ agregar {f['suggest_kwarg']}=<N>"
                              + (f"   [{f['note']}]" if f.get("note") else ""))
                    else:
                        print(f"  ▲ {rel}:{f['line']}  {f['constructor']}: {f['why']}")
            print(f"\n  {total} finding(s) in {len(per_file)} file(s) "
                  f"({scanned} scanned). Use --patch to emit a unified diff.")
        if args.patch:
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
        print(f"gasket: internal error: {type(e).__name__}: {e}", file=sys.stderr)
        return 2


def main(argv=None) -> int:
    p = argparse.ArgumentParser(
        prog="gasket",
        description="Static budget certificates for LLM-agent workflows "
                    "(LangGraph / CrewAI / OpenAI Agents SDK). Never executes your code.")
    p.add_argument("--version", action="version", version=f"gasket {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="map workflows to the typed-budget calculus and report bounds")
    c.add_argument("path", nargs="?", default=".")
    c.add_argument("--json", action="store_true", help="emit gasket.v1 JSON")
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

    from gasket.pack import cmd_pack
    pk = sub.add_parser("pack", help="build a deterministic .py-only tarball for server-side certification")
    pk.add_argument("path", nargs="?", default=".")
    pk.add_argument("-o", "--output", default="gasket-artifact.tgz")
    pk.set_defaults(fn=cmd_pack)

    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
