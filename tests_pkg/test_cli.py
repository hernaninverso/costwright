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


def test_aliased_import_constructors_are_detected(tmp_path):
    # codex/Cursor r81: `from langgraph.graph import StateGraph as SG` / `from langchain_openai import
    # ChatOpenAI as LLM2` — an aliased framework constructor escaped by-name detection, so the graph unit / the
    # uncapped LLM was silently dropped (false 'all good'/'all capped'). Both now resolve the import alias.
    import ast as _ast
    from costwright import caps as cm
    # caps: aliased uncapped LLM is flagged; make_patch matches the alias and stays valid Python
    (tmp_path / "m.py").write_text("from langchain_openai import ChatOpenAI as LLM2\nllm = LLM2(model='gpt-4o')\n")
    fs, src = cm.scan_file(tmp_path / "m.py")
    assert [(x["kind"], x["constructor"]) for x in fs] == [("missing", "LLM2")], fs
    added = [ln[1:] for ln in cm.make_patch(Path("m.py"), src, fs, 100).splitlines()
             if ln.startswith("+") and not ln.startswith("+++")]
    assert added and "max_tokens=100" in added[0]
    _ast.parse(added[0].strip())   # valid Python
    # an aliased reasoning model still gets the correct kwarg
    (tmp_path / "r.py").write_text("from langchain_openai import ChatOpenAI as LLM2\nllm = LLM2('gpt-5')\n")
    assert cm.scan_file(tmp_path / "r.py")[0][0]["suggest_kwarg"] == "max_completion_tokens"
    # cli._find_units: an aliased StateGraph is still discovered as a langgraph unit
    make(tmp_path, "g.py",
         "from langgraph.graph import StateGraph as SG, START, END\n"
         "g = SG(dict)\ng.add_node('x', lambda s: s)\n"
         "g.add_edge(START, 'x'); g.add_edge('x', END)\n"
         "g.compile().invoke({}, config={'recursion_limit': 5})\n")
    rep = json.loads(run("check", str(tmp_path), "--json").stdout)
    assert any(u["framework"] == "langgraph" for u in rep["units"]), rep


def test_caps_dynamic_model_requires_max_completion_tokens(tmp_path):
    # codex r83: a Chat-API model name that is NOT a string constant — `"gpt-" + "5"` (BinOp concat), a Name
    # bound elsewhere, an f-string, os.environ[...] — could resolve to a reasoning model at runtime, where
    # max_tokens is IGNORED. Before the fix the analyzer saw a non-constant model ⇒ assumed non-reasoning ⇒
    # accepted `max_tokens=1` as an effective cap ⇒ "all capped" false assurance. Now a dynamic model on
    # ChatOpenAI/AzureChatOpenAI requires max_completion_tokens (the cap that holds for reasoning AND
    # non-reasoning Chat models); a max_tokens-only call is flagged 'ineffective'.
    from costwright import caps as cm
    flagged = {
        'from langchain_openai import ChatOpenAI\nx = ChatOpenAI(model="gpt-"+"5", max_tokens=1)\n',  # concat
        'from langchain_openai import ChatOpenAI\nx = ChatOpenAI(model=m, max_tokens=1)\n',           # Name var
        'from langchain_openai import AzureChatOpenAI\nx = AzureChatOpenAI(model=f"{base}", max_tokens=1)\n',
    }
    for code in flagged:
        f = tmp_path / "dyn.py"
        f.write_text(code)
        fs, _ = cm.scan_file(f)
        assert fs and fs[0]["kind"] == "ineffective" \
            and fs[0]["suggest_kwarg"] == "max_completion_tokens", (code, fs)
    # NO false positives: a dynamic model WITH max_completion_tokens is effective; a CONSTANT non-reasoning
    # model with max_tokens is fine; an absent model arg keeps the non-reasoning default assumption.
    clean = {
        'from langchain_openai import ChatOpenAI\nx = ChatOpenAI(model=m, max_completion_tokens=100)\n',
        'from langchain_openai import ChatOpenAI\nx = ChatOpenAI(model="gpt-4o", max_tokens=1)\n',
        'from langchain_openai import ChatOpenAI\nx = ChatOpenAI(max_tokens=1)\n',
    }
    for code in clean:
        f = tmp_path / "ok.py"
        f.write_text(code)
        assert cm.scan_file(f)[0] == [], code


def test_caps_reflective_getattr_constructor_fails_closed(tmp_path):
    # Cursor r83: `importlib.import_module("langchain_openai")` + `getattr(m, "ChatOpenAI")` then `Ctor(...)`
    # reaches a known LLM constructor by STRING; the eventual call carries the name `Ctor`, so by-name detection
    # missed it and the CLI printed "all LLM constructors capped" (false assurance). A literal getattr of a known
    # constructor now fails closed, and the all-clear message no longer claims completeness.
    from costwright import caps as cm
    witness = ('import importlib\nm = importlib.import_module("langchain_openai")\n'
               'Ctor = getattr(m, "ChatOpenAI")\nllm = Ctor(model="gpt-4o")\n')
    f = tmp_path / "refl.py"
    f.write_text(witness)
    fs, _ = cm.scan_file(f)
    assert fs and fs[0]["kind"] == "reflective" and fs[0]["constructor"] == "ChatOpenAI", fs
    # a getattr of an UNKNOWN name is not flagged (no noise); a non-literal getattr does not crash
    (tmp_path / "n.py").write_text('x = getattr(mod, "NotAConstructor")\ny = getattr(mod, var)\n')
    assert cm.scan_file(tmp_path / "n.py")[0] == []
    # the CLI all-clear wording for a genuinely-clean file must NOT overclaim "all capped"
    d = tmp_path / "cleandir"; d.mkdir()
    (d / "ok.py").write_text('from langchain_openai import ChatOpenAI\nllm = ChatOpenAI(model="gpt-4o", max_tokens=128)\n')
    out = run("caps", str(d)).stdout
    assert "all LLM constructors capped" not in out and "not covered" in out, out


def test_caps_make_patch_valid_python_and_correct_kwarg(tmp_path):
    # codex/Cursor r76: make_patch inserted the kwarg right after '(', producing Ctor(kwarg=…, "positional") =
    # SyntaxError; and a reasoning model passed POSITIONALLY (ChatOpenAI("gpt-5")) escaped reasoning detection
    # so the WRONG kwarg (max_tokens) was suggested. Now the kwarg is the LAST arg (AST-based) and a positional
    # model is recognized. Every suggested edit must PARSE and use the constructor's correct kwarg.
    import ast as _ast
    from costwright import caps as cm
    cases = {
        'from x import ChatOpenAI\nllm = ChatOpenAI("gpt-5")\n': "max_completion_tokens",   # reasoning, positional
        'from x import ChatOpenAI\nllm = ChatOpenAI("gpt-4o", temperature=0)\n': "max_tokens",
        'from x import ChatOllama\nllm = ChatOllama("llama3")\n': "num_predict",
    }
    for code, expect_kwarg in cases.items():
        f = tmp_path / "p.py"
        f.write_text(code)
        fs, src = cm.scan_file(f)
        assert fs and fs[0]["suggest_kwarg"] == expect_kwarg, (code, fs)
        patch = cm.make_patch(Path("p.py"), src, fs, 100)
        added = [ln[1:] for ln in patch.splitlines() if ln.startswith("+") and not ln.startswith("+++")]
        assert added, (code, patch)
        for ln in added:
            _ast.parse(ln.strip())                # must be valid Python (raises SyntaxError otherwise)
            assert f"{expect_kwarg}=100" in ln, (code, ln)


def test_caps_patch_rejects_nonpositive_cap(tmp_path):
    # codex r75: --patch --cap 0 would insert an inert max_tokens=0 (which costwright itself flags ineffective).
    make(tmp_path, "a.py", "from x import ChatOpenAI\nllm=ChatOpenAI(model='gpt-4')\n")
    assert run("caps", str(tmp_path), "--patch", "-", "--cap", "0").returncode == 2
    r = run("caps", str(tmp_path), "--patch", "-", "--cap", "256")
    assert r.returncode == 0 and "max_tokens=256" in r.stdout


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
