"""gasket pack — empaqueta un repo en un tarball REPRODUCIBLE para certificación server-side.

Determinista: mismo árbol .py → mismo tarball byte-idéntico (orden estable, mtime/uid/gid fijos,
solo .py, exclusiones estándar). El servicio de certificación corre gasket sobre ESTO, no sobre un
reporte que el cliente podría fabricar.
"""
import io
import tarfile
from pathlib import Path

EXCLUDE_DIRS = {".venv", "venv", "node_modules", "site-packages", ".git", "__pycache__",
                "tests", "test", "docs", "build", "dist"}
MAX_FILES = 5000
MAX_TOTAL_BYTES = 50 * 1024 * 1024  # 50 MB de fuente .py es muchísimo; guard


def build_tarball(root: Path) -> bytes:
    """Tarball .py determinista. Lanza ValueError si excede los límites."""
    files = []
    total = 0
    for py in sorted(root.rglob("*.py")):
        if py.is_symlink() or any(p.is_symlink() for p in py.parents
                                  if root in p.parents or p == root):
            continue
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        rel = py.relative_to(root).as_posix()
        if ".." in rel.split("/") or rel.startswith("/"):
            continue
        data = py.read_bytes()
        total += len(data)
        if len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES:
            raise ValueError("artifact too large (file count or total bytes exceeded)")
        files.append((rel, data))
    buf = io.BytesIO()
    # gzip sin mtime (determinismo): escribir tar sin compresión y comprimir aparte con mtime=0
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for rel, data in files:                       # ya ordenado
            ti = tarfile.TarInfo(name=rel)
            ti.size = len(data)
            ti.mtime = 0
            ti.uid = ti.gid = 0
            ti.uname = ti.gname = ""
            ti.mode = 0o644
            tf.addfile(ti, io.BytesIO(data))
    import gzip
    with gzip.GzipFile(fileobj=buf, mode="wb", mtime=0) as gz:
        gz.write(raw.getvalue())
    return buf.getvalue()


def cmd_pack(args) -> int:
    import sys
    root = Path(args.path).resolve()
    if not root.exists():
        print(f"gasket: path not found: {root}", file=sys.stderr)
        return 2
    try:
        tgz = build_tarball(root)
    except ValueError as e:
        print(f"gasket pack: {e}", file=sys.stderr)
        return 2
    out = Path(args.output)
    out.write_bytes(tgz)
    import hashlib
    print(f"gasket pack: wrote {out} ({len(tgz)} bytes, sha256 {hashlib.sha256(tgz).hexdigest()[:16]}…)")
    print("  upload this artifact to the certification service; it re-runs gasket server-side.")
    return 0
