"""Tests e2e del CLI gasket (spec 002 FR-004): exit codes, JSON schema golden, caps, patch."""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

def run(*args, cwd=None):
    return subprocess.run([PY, "-m", "gasket.cli", *args], capture_output=True,
                          text=True, cwd=cwd, env={"PYTHONPATH": str(ROOT / "src"), "PATH": "/usr/bin:/bin"})

FIX_DEFAULT = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("agent", lambda s: s)
g.add_node("tools", lambda s: s)
g.add_conditional_edges("agent", lambda s: "tools", {"tools": "tools", "end": "END"})
g.add_edge("tools", "agent")
app = g.compile()
app.invoke({})
'''
FIX_RUNAWAY = '''
from langgraph.graph import StateGraph
g = StateGraph(dict)
g.add_node("a", lambda s: s)
app = g.compile()
while True:
    app.invoke({})
'''
FIX_NOCAP = '''
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="gpt-4o")
'''
FIX_GEMINI_DEGRADED = '''
from langchain_google_genai import ChatGoogleGenerativeAI
llm = ChatGoogleGenerativeAI(model="gemini-2.5-pro", max_output_tokens=512)
'''

def make(tmp, name, code):
    p = Path(tmp) / name; p.write_text(code); return p

def test_exit_0_default():
    with tempfile.TemporaryDirectory() as td:
        make(td, "wf.py", FIX_DEFAULT)
        r = run("check", td)
        assert r.returncode == 0, (r.returncode, r.stderr)       # council P0-1: default nunca falla
        assert "default_dependent" in r.stdout

def test_fail_on_policy():
    with tempfile.TemporaryDirectory() as td:
        make(td, "wf.py", FIX_RUNAWAY)
        assert run("check", td).returncode == 0                   # sin política: 0
        r = run("check", td, "--fail-on", "reject")
        assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
        assert "policy" in r.stderr

def test_exit_2_bad_path():
    r = run("check", "/nonexistent/xyz")
    assert r.returncode == 2

def test_json_schema_golden():
    with tempfile.TemporaryDirectory() as td:
        make(td, "wf.py", FIX_DEFAULT)
        r = run("check", td, "--json")
        rep = json.loads(r.stdout)
        assert rep["schema"] == "gasket.v1"
        assert set(rep["summary"]) == {"total", "certifiable", "default_dependent",
                                       "non_certifiable", "runaway", "parse_error",
                                       "vacuous_default_bounds"}
        u = rep["units"][0]
        assert set(u) == {"unit_id", "file", "span", "framework", "category", "bound", "reasons"}
        assert u["category"] in ("certifiable", "default_dependent", "non_certifiable",
                                 "runaway", "parse_error")
        assert u["bound"]["provenance"] in ("explicit", "framework_default", "absent")
        assert rep["signature"] is None                            # reservado E1
        # CI-log safe: el JSON no incluye código fuente
        assert "add_node" not in r.stdout

def test_caps_missing_y_nota_provider():
    with tempfile.TemporaryDirectory() as td:
        make(td, "m.py", FIX_NOCAP)
        r = run("caps", td)
        assert r.returncode == 0
        assert "max_tokens" in r.stdout and "ChatOpenAI" in r.stdout

def test_caps_gemini_degraded():
    with tempfile.TemporaryDirectory() as td:
        make(td, "g.py", FIX_GEMINI_DEGRADED)
        r = run("caps", td)
        assert "thinking_budget" in r.stdout, r.stdout             # degradación §3.2

def test_caps_patch_no_edita():
    with tempfile.TemporaryDirectory() as td:
        p = make(td, "m.py", FIX_NOCAP)
        before = p.read_text()
        r = run("caps", td, "--patch", "-", "--cap", "777")
        assert "max_tokens=777" in r.stdout                        # diff en stdout
        assert p.read_text() == before                             # JAMÁS edita (council P0-2)
        assert r.stdout.count("+++") == 1

def test_no_units():
    with tempfile.TemporaryDirectory() as td:
        make(td, "x.py", "print('hola')\n")
        r = run("check", td)
        assert r.returncode == 0 and "no graph units" in r.stdout

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
