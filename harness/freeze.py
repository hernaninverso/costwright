"""F1 — freeze: detecta graph units (D1) en dataset/repos/, dedup estructural (D6c), congela.

Unidad = graph object (StateGraph compilado / Crew / Runner entrypoint), NO archivo.
Salida: dataset/frozen/<unit_id>/ + dataset/frozen_manifest.jsonl + freeze_log.jsonl (descartes).
"""
import ast, hashlib, json, shutil, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
REPOS = ROOT / "dataset" / "repos"
FROZEN = ROOT / "dataset" / "frozen"
EXCLUDE_DIRS = {".venv", "venv", "node_modules", "site-packages", ".git", "__pycache__",
                "tests", "test", "docs"}  # tests/docs: no son workflows de producción

def repo_meta():
    meta = {}
    mf = ROOT / "dataset" / "repos_manifest.jsonl"
    for line in mf.read_text().splitlines():
        d = json.loads(line)
        # collect2.sh usó `tr '/' '__'` que es char-a-char ⟹ '/' → '_' (UN underscore)
        meta[d["repo"].replace("/", "_")] = d
    return meta

class UnitFinder(ast.NodeVisitor):
    """Encuentra constructores de graph units y la región (función/módulo) que los contiene."""
    def __init__(s, src):
        s.units = []          # (kind, lineno, scope_node)
        s.scope_stack = []
        s.src = src
    def visit_FunctionDef(s, n): s._scope(n)
    def visit_AsyncFunctionDef(s, n): s._scope(n)
    def visit_ClassDef(s, n): s._scope(n)
    def _scope(s, n):
        s.scope_stack.append(n); s.generic_visit(n); s.scope_stack.pop()
    def visit_Call(s, n):
        name = call_name(n)
        kind = None
        if name == "StateGraph": kind = "langgraph"
        elif name == "Crew": kind = "crewai"
        elif name in ("Runner.run", "Runner.run_sync", "Runner.run_streamed"): kind = "agents_sdk"
        if kind:
            scope = s.scope_stack[-1] if s.scope_stack else None
            s.units.append((kind, n.lineno, scope))
        s.generic_visit(n)

def call_name(n: ast.Call) -> str:
    f = n.func
    if isinstance(f, ast.Name): return f.id
    if isinstance(f, ast.Attribute):
        if isinstance(f.value, ast.Name): return f"{f.value.id}.{f.attr}"
        return f.attr
    return ""

class Normalizer(ast.NodeTransformer):
    """AST normalizado para dedup estructural: ids→_, sin literales, sin docstrings."""
    def visit_Name(s, n): n.id = "_"; return n
    def visit_arg(s, n): n.arg = "_"; return n
    def visit_FunctionDef(s, n):
        n.name = "_"; s._strip_doc(n); s.generic_visit(n); return n
    def visit_AsyncFunctionDef(s, n):
        n.name = "_"; s._strip_doc(n); s.generic_visit(n); return n
    def visit_ClassDef(s, n):
        n.name = "_"; s._strip_doc(n); s.generic_visit(n); return n
    def _strip_doc(s, n):
        if n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant):
            n.body = n.body[1:] or [ast.Pass()]
    def visit_Constant(s, n): n.value = None; return n
    def visit_Attribute(s, n): s.generic_visit(n); return n  # conservar attrs (add_node, etc.)

def structural_hash(scope_node, full_tree, src: str) -> str:
    node = scope_node if scope_node is not None else full_tree
    try:
        norm = Normalizer().visit(ast.parse(ast.unparse(node)))
        dump = ast.dump(norm, annotate_fields=False)
    except Exception:
        dump = ast.dump(node, annotate_fields=False)
    return hashlib.sha256(dump.encode()).hexdigest()[:16]

def main():
    meta = repo_meta()
    if FROZEN.exists(): shutil.rmtree(FROZEN)
    FROZEN.mkdir(parents=True)
    manifest, log, seen = [], [], {}
    uid = 0
    for repo_dir in sorted(REPOS.iterdir()):
        if not repo_dir.is_dir(): continue
        m = meta.get(repo_dir.name, {})
        for py in sorted(repo_dir.rglob("*.py")):
            if any(part in EXCLUDE_DIRS for part in py.parts): continue
            try:
                src = py.read_text(encoding="utf-8", errors="ignore")
            except Exception:
                continue
            if not any(k in src for k in ("StateGraph", "Crew(", "Runner.run")): continue
            try:
                tree = ast.parse(src)
            except SyntaxError as e:
                log.append({"file": str(py.relative_to(REPOS)), "discard": "syntax-error", "err": str(e)[:80]})
                continue
            f = UnitFinder(src); f.visit(tree)
            for kind, lineno, scope in f.units:
                h = structural_hash(scope, tree, src)
                key = (kind, h)
                if key in seen:
                    log.append({"file": str(py.relative_to(REPOS)), "line": lineno, "kind": kind,
                                "discard": "structural-dup", "dup_of": seen[key]})
                    continue
                uid += 1
                unit_id = f"u{uid:03d}"
                seen[key] = unit_id
                d = FROZEN / unit_id; d.mkdir()
                shutil.copy2(py, d / py.name)
                manifest.append({
                    "unit_id": unit_id, "kind": kind, "line": lineno,
                    "repo": m.get("repo", repo_dir.name), "sha": m.get("sha", "?"),
                    "stars": m.get("stars", 0),
                    "bucket": "0-10" if m.get("stars", 0) <= 10 else ("11-100" if m.get("stars", 0) <= 100 else ">100"),
                    "file": py.name, "rel_path": str(py.relative_to(REPOS)),
                    "scope": getattr(scope, "name", "<module>") if scope is not None else "<module>",
                    "struct_hash": h,
                })
    (ROOT / "dataset" / "frozen_manifest.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in manifest) + "\n")
    (ROOT / "dataset" / "freeze_log.jsonl").write_text(
        "\n".join(json.dumps(x, ensure_ascii=False) for x in log) + "\n" if log else "")
    by_kind = {}
    for x in manifest: by_kind[x["kind"]] = by_kind.get(x["kind"], 0) + 1
    by_bucket = {}
    for x in manifest: by_bucket[x["bucket"]] = by_bucket.get(x["bucket"], 0) + 1
    print(f"UNITS ÚNICOS: {len(manifest)}  por-kind: {by_kind}  por-bucket: {by_bucket}")
    print(f"descartes: {len(log)} (dup/syntax) — gate F1 {'PASS' if len(manifest) >= 50 else 'FAIL (<50)'}")

if __name__ == "__main__":
    main()
