"""Tests e2e del CLI costwright (spec 002 FR-004): exit codes, JSON schema golden, caps, patch."""
import json, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

def run(*args, cwd=None):
    return subprocess.run([PY, "-m", "costwright.cli", *args], capture_output=True,
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

FIX_NONCERT = '''
from langgraph.graph import StateGraph, START, END
g = StateGraph(dict)
getattr(g, "add_node")("a", lambda s: s)   # obscured add_node call ⇒ no-mapeable:addnode-escaped (non_certifiable)
g.add_edge(START, "a"); g.add_edge("a", END)
g.compile().invoke({}, config={"recursion_limit": 5})
'''
FIX_PARSEERR = '''
from langgraph.graph import StateGraph, START, END
g = StateGraph(dict)
g.add_node("a", lambda s: s)
g.add_edge(START, "a"); g.add_edge("a", END)
g.compile().invoke({}, config={"recursion_limit": -5})   # nonpositive ⇒ extractor-failure ⇒ parse_error
'''

def test_fail_on_tiers_monotonic(tmp_path):
    # codex r72: tiers must be MONOTONIC and complete. A non_certifiable unit must trip both the
    # non-certifiable AND the (stricter) default-dependent tier; a parse_error (couldn't analyze) must trip
    # both too (never silently pass). reject (runaway only) stays the loosest.
    import subprocess
    for fix, expect in ((FIX_NONCERT, "non_certifiable"), (FIX_PARSEERR, "parse_error")):
        with tempfile.TemporaryDirectory() as td:
            make(td, "wf.py", fix)
            assert run("check", td, "--fail-on", "non-certifiable").returncode == 1, expect
            assert run("check", td, "--fail-on", "default-dependent").returncode == 1, expect
            # reject only fails on runaway → these pass reject
            assert run("check", td, "--fail-on", "reject").returncode == 0, expect


def test_caps_ineffective_cap_flagged(tmp_path):
    # codex r72/r73: a cap kwarg is EFFECTIVE only if it is the constructor's CORRECT kwarg present as a
    # positive-int literal. A None/negative/variable/True value, or the WRONG kwarg for the constructor
    # (max_tokens on OpenAI's Responses API, whose cap is max_output_tokens; max_tokens on ChatOllama, whose
    # cap is num_predict), is NOT effective and must be flagged.
    from costwright import caps as cm
    cases = (
        ("from x import ChatOpenAI\nllm=ChatOpenAI(model='gpt-4', max_tokens=None)", "ineffective"),
        ("from x import ChatOpenAI\nL=5\nllm=ChatOpenAI(model='gpt-4', max_tokens=L)", "ineffective"),
        ("from x import OpenAI\nllm=OpenAI(max_tokens=10)", "ineffective"),          # wrong kwarg for Responses
        ("from x import ChatOllama\nllm=ChatOllama(max_tokens=100)", "ineffective"),  # Ollama needs num_predict
        ("from x import ChatOpenAI\nllm=ChatOpenAI(model='gpt-4')", "missing"),
        ("from x import OpenAI\nllm=OpenAI(max_output_tokens=10)", None),             # correct
        ("from x import ChatOpenAI\nllm=ChatOpenAI(model='gpt-4', max_tokens=128)", None),
        ("from x import ChatOllama\nllm=ChatOllama(num_predict=100)", None),
    )
    for code, kind in cases:
        (tmp_path / "m.py").write_text(code)
        kinds = [x["kind"] for x in cm.scan_file(tmp_path / "m.py")[0]]
        if kind is None:
            assert kinds == [], (code, kinds)
        else:
            assert kind in kinds, (code, kinds)


def test_caps_syntax_error_not_all_capped(tmp_path):
    # codex r73: a file that does NOT parse but mentions an LLM constructor could hide an uncapped one;
    # reporting "all capped" is false assurance. It now surfaces a parse_error finding; an unrelated broken
    # file (no LLM constructor text) is skipped (no noise).
    from costwright import caps as cm
    (tmp_path / "broken.py").write_text("from x import ChatOpenAI\nChatOpenAI(max_tokens=10")  # syntax error
    assert [f["kind"] for f in cm.scan_file(tmp_path / "broken.py")[0]] == ["parse_error"]
    (tmp_path / "unrelated.py").write_text("def f(:\n  pass")  # broken, no LLM ctor
    assert cm.scan_file(tmp_path / "unrelated.py")[0] == []
    r = run("caps", str(tmp_path))
    assert "all LLM constructors capped" not in r.stdout, r.stdout


def test_exit_2_bad_path():
    r = run("check", "/nonexistent/xyz")
    assert r.returncode == 2

def test_json_schema_golden():
    with tempfile.TemporaryDirectory() as td:
        make(td, "wf.py", FIX_DEFAULT)
        r = run("check", td, "--json")
        rep = json.loads(r.stdout)
        assert rep["schema"] == "costwright.v1"
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


# ── fixes audit-3 ──
def test_bound_no_subreporta():
    # ciclo → aggregation=sum → el ceiling real (supersteps×nodos) DEBE estar en el JSON y pretty
    code = FIX_DEFAULT
    with tempfile.TemporaryDirectory() as td:
        make(td, "wf.py", code)
        r = run("check", td, "--json")
        u = json.loads(r.stdout)["units"][0]
        assert u["bound"]["aggregation"] == "sum"
        assert u["bound"]["node_executions_ceiling"] == u["bound"]["supersteps"] * 2, u["bound"]
        r2 = run("check", td)
        assert "node-executions" in r2.stdout, r2.stdout

def test_anthropic_degraded_warning():
    code = '''
from langchain_anthropic import ChatAnthropic
llm = ChatAnthropic(model="claude-sonnet-4-6", max_tokens=2048)
'''
    with tempfile.TemporaryDirectory() as td:
        make(td, "a.py", code)
        r = run("caps", td)
        assert "interleaved" in r.stdout, r.stdout

def test_reasoning_model_sugiere_completion_tokens():
    code = '''
from langchain_openai import ChatOpenAI
llm = ChatOpenAI(model="o3-mini")
'''
    with tempfile.TemporaryDirectory() as td:
        make(td, "r.py", code)
        r = run("caps", td)
        assert "max_completion_tokens" in r.stdout, r.stdout

def test_patch_same_line_doble_no_corrompe():
    code = 'from langchain_openai import ChatOpenAI\na = ChatOpenAI(model="x"); b = ChatOpenAI(model="y")\n'
    with tempfile.TemporaryDirectory() as td:
        p = make(td, "d.py", code)
        r = run("caps", td, "--patch", "-")
        # 2 hallazgos pero 0 parches (misma línea, conservador): el diff debe estar VACÍO
        assert "ChatOpenAI" in run("caps", td).stdout
        assert r.stdout.count("+++") == 0, r.stdout
        assert p.read_text() == code

def test_symlink_no_se_sigue():
    with tempfile.TemporaryDirectory() as td, tempfile.TemporaryDirectory() as outside:
        make(outside, "evil.py", FIX_RUNAWAY)
        (Path(td) / "link").symlink_to(outside)
        r = run("check", td, "--json")
        assert json.loads(r.stdout)["summary"]["total"] == 0 if r.stdout.strip().startswith("{") else "no graph units" in r.stdout

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
