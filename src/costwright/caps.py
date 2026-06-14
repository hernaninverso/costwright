"""costwright caps — detección de constructores LLM sin cap de tokens + sugerencia por provider.

La tabla provider→parámetro proviene de §3.2 del paper (verificada contra docs primarias jun-2026):
el cap correcto es PARAMETER-specific, no provider-specific. NUNCA edita archivos: emite hallazgos
y, con --patch, un unified diff aplicable con `git apply` (decisión del council 002: P0-2).
"""
import ast
import difflib
from pathlib import Path

# constructor → (provider, kwarg correcto, nota de degradación si aplica)
# Fuente: paper §3.2, docs primarias accedidas jun-2026.
PROVIDER_CAPS = {
    # OpenAI / Azure (langchain + SDKs): chat completions usa max_tokens (no-reasoning) o
    # max_completion_tokens (reasoning); Responses API usa max_output_tokens.
    "ChatOpenAI":        ("openai",    "max_tokens",        "reasoning models: usar max_completion_tokens (Chat) / max_output_tokens (Responses)"),
    "AzureChatOpenAI":   ("azure",     "max_tokens",        "reasoning models: max_completion_tokens — reasoning_tokens ⊆ completion_tokens (cap REAL)"),
    "OpenAI":            ("openai",    "max_output_tokens", "Responses API: bounds reasoning+output"),
    # Anthropic
    "ChatAnthropic":     ("anthropic", "max_tokens",        "standard: budget_tokens < max_tokens ⟹ techo real. interleaved/adaptive thinking: el budget puede EXCEDER max_tokens (cap degrada)"),
    "Anthropic":         ("anthropic", "max_tokens",        "ídem ChatAnthropic"),
    # Google
    "ChatGoogleGenerativeAI": ("gemini", "max_output_tokens", "thinking on: fijar TAMBIÉN thinking_budget — maxOutputTokens NO acota thinking (se factura aparte)"),
    "ChatVertexAI":      ("gemini",    "max_output_tokens", "ídem Gemini"),
    # otros (langchain)
    "ChatBedrock":       ("bedrock",   "max_tokens",        "replica la semántica Anthropic en modelos Claude"),
    "ChatGroq":          ("groq",      "max_tokens",        None),
    "ChatMistralAI":     ("mistral",   "max_tokens",        None),
    "ChatOllama":        ("ollama",    "num_predict",       "Ollama usa num_predict, no max_tokens"),
    "init_chat_model":   ("generic",   "max_tokens",        "el kwarg efectivo depende del provider resuelto en runtime — verificar"),
    "LLM":               ("crewai",    "max_tokens",        "CrewAI LLM wrapper"),
}
CAP_KWARGS = {"max_tokens", "max_output_tokens", "max_completion_tokens", "budget_tokens",
              "max_tokens_to_sample", "maxOutputTokens", "num_predict", "thinking_budget"}
EXCLUDE_DIRS = {".venv", "venv", "node_modules", "site-packages", ".git", "__pycache__"}


def call_name(n: ast.Call) -> str:
    f = n.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def scan_file(path: Path):
    """Devuelve CapFindings: constructores LLM sin ningún cap kwarg."""
    try:
        src = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return [], None
    try:
        tree = ast.parse(src)
    except SyntaxError:
        # a file that DOESN'T PARSE could hide an uncapped LLM constructor — reporting "all capped" for it is
        # false assurance (codex r73). If the text mentions a known LLM constructor, surface that the caps
        # could NOT be verified; an unrelated broken file (no LLM constructor text) is skipped (no noise).
        if any(c in src for c in PROVIDER_CAPS):
            return [{"kind": "parse_error", "constructor": None, "provider": None, "line": 0,
                     "suggest_kwarg": None,
                     "why": "el archivo no parsea (SyntaxError) y menciona un constructor LLM — los token-caps "
                            "NO se pudieron verificar; no asumir que están acotados"}], src
        return [], None
    # `from langchain_openai import ChatOpenAI as LLM2` → an aliased constructor escapes the by-name lookup and
    # its missing cap is silently NOT reported (codex/Cursor r81). Resolve `from X import Ctor as local` so the
    # aliased call matches PROVIDER_CAPS.
    alias = {a.asname: a.name for nd in ast.walk(tree) if isinstance(nd, ast.ImportFrom)
             for a in nd.names if a.asname}
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)                 # source name (what make_patch matches + what we display)
        # REFLECTIVE construction (Cursor r83): `getattr(mod, "ChatOpenAI")` / `importlib.import_module(...)`
        # then `getattr(...)` reaches a known LLM constructor by STRING, escaping by-name call detection — the
        # eventual `Ctor(...)` call carries the name `Ctor`, not `ChatOpenAI`. We cannot attribute that call's
        # kwargs, so its token-cap is NOT statically verifiable → fail closed (a finding), never "all capped".
        if name == "getattr" and len(node.args) >= 2 and isinstance(node.args[1], ast.Constant) \
                and isinstance(node.args[1].value, str) and node.args[1].value in PROVIDER_CAPS:
            ctor = node.args[1].value
            findings.append({
                "kind": "reflective", "constructor": ctor, "provider": PROVIDER_CAPS[ctor][0],
                "line": node.lineno, "suggest_kwarg": None,
                "why": f"constructor `{ctor}` accedido reflectivamente vía getattr — la llamada y sus kwargs no "
                       "se pueden atribuir estáticamente; el token-cap NO se puede verificar (fail closed)",
            })
            continue
        resolved = alias.get(name, name)       # the real constructor, for the provider/kwarg lookup
        if resolved not in PROVIDER_CAPS:
            continue
        kwargs_present = {k.arg for k in node.keywords if k.arg}
        provider, kwarg, note = PROVIDER_CAPS[resolved]
        # detección best-effort de reasoning model por el kwarg `model` (audit-3 gpt-5.5 P0):
        # en Chat API los o-series/GPT-5 ignoran max_tokens; el cap real es max_completion_tokens
        model_val = next((k.value.value for k in node.keywords
                          if k.arg == "model" and isinstance(k.value, ast.Constant)
                          and isinstance(k.value.value, str)), "")
        # the model can also be the FIRST POSITIONAL arg — `ChatOpenAI("gpt-5")` (codex/Cursor r76); otherwise
        # a reasoning model passed positionally would escape the reasoning detection below.
        if not model_val and node.args and isinstance(node.args[0], ast.Constant) \
                and isinstance(node.args[0].value, str):
            model_val = node.args[0].value
        reasoning = any(model_val.startswith(p) for p in
                        ("o1", "o3", "o4", "gpt-5")) if model_val else False
        # the model arg may be PRESENT but NOT a resolvable string constant — `"gpt-" + "5"` (BinOp concat),
        # a Name bound elsewhere, an f-string, `os.environ[...]` (codex r83). We then CANNOT tell whether it
        # resolves to a reasoning model, so we cannot certify `max_tokens` is effective.
        model_node = next((k.value for k in node.keywords if k.arg == "model"), None)
        if model_node is None and node.args:
            model_node = node.args[0]
        model_dynamic = model_node is not None and not (
            isinstance(model_node, ast.Constant) and isinstance(model_node.value, str))
        # SOLO Chat-API constructors (audit-3 R2 gpt-5.5): el constructor `OpenAI` es
        # Responses API y su cap correcto sigue siendo max_output_tokens, reasoning o no
        if resolved in ("ChatOpenAI", "AzureChatOpenAI") and reasoning:
            kwarg = "max_completion_tokens"
            note = "reasoning model en Chat API: max_tokens es IGNORADO; usar max_completion_tokens"
        elif resolved in ("ChatOpenAI", "AzureChatOpenAI") and model_dynamic:
            # a dynamic model name could resolve to a reasoning model at runtime, where Chat-API max_tokens is
            # IGNORED. The only cap that holds REGARDLESS is max_completion_tokens (caps reasoning AND
            # non-reasoning Chat models). Require it; fail closed on a max_tokens-only call (codex r83).
            kwarg = "max_completion_tokens"
            note = ("modelo dinámico (no es una constante): si resuelve a un reasoning model, max_tokens es "
                    "IGNORADO — usar max_completion_tokens (acota reasoning y no-reasoning)")
        # an EFFECTIVE cap = the CONSTRUCTOR'S correct kwarg (post reasoning-adjustment) present as a positive
        # integer LITERAL. A cap kwarg that is the WRONG one for this constructor (e.g. max_tokens on OpenAI's
        # Responses API, whose cap is max_output_tokens — codex r73) or whose value is None/negative/variable/
        # True (codex r72) is NOT effective.
        correct_effective = any(k.arg == kwarg and isinstance(k.value, ast.Constant)
                                and isinstance(k.value.value, int) and not isinstance(k.value.value, bool)
                                and k.value.value > 0 for k in node.keywords)
        if not correct_effective:
            if kwargs_present & CAP_KWARGS:
                findings.append({
                    "kind": "ineffective", "constructor": name, "provider": provider,
                    "line": node.lineno, "have": sorted(kwargs_present & CAP_KWARGS), "suggest_kwarg": kwarg,
                    "why": f"el cap presente no acota: falta el kwarg correcto `{kwarg}` como entero positivo "
                           "(o el valor es None/negativo/variable/True, o es el kwarg equivocado para este "
                           "constructor)",
                })
                continue
            findings.append({
                "kind": "missing", "constructor": name, "provider": provider,
                "line": node.lineno, "suggest_kwarg": kwarg, "note": note,
            })
            continue
        if correct_effective:
            # tiene un cap EFECTIVO (el kwarg correcto, entero positivo) — chequear degradaciones conocidas (§3.2)
            if provider == "gemini" and "thinking_budget" not in kwargs_present:
                findings.append({
                    "kind": "degraded", "constructor": name, "provider": provider,
                    "line": node.lineno, "have": sorted(kwargs_present & CAP_KWARGS),
                    "suggest_kwarg": "thinking_budget",
                    "why": "Gemini: maxOutputTokens NO acota thinking tokens (se facturan como output); fijar thinking_budget",
                })
            elif provider in ("anthropic", "bedrock"):
                # audit-3 (gemini P0): Anthropic con cap igual degrada bajo interleaved/adaptive
                findings.append({
                    "kind": "degraded", "constructor": name, "provider": provider,
                    "line": node.lineno, "have": sorted(kwargs_present & CAP_KWARGS),
                    "suggest_kwarg": None,
                    "why": "Anthropic: con interleaved/adaptive thinking el budget puede EXCEDER max_tokens — el techo solo vale en modo standard (budget_tokens < max_tokens)",
                })
            elif resolved in ("ChatOpenAI", "AzureChatOpenAI") and reasoning and "max_completion_tokens" not in kwargs_present:
                findings.append({
                    "kind": "degraded", "constructor": name, "provider": provider,
                    "line": node.lineno, "have": sorted(kwargs_present & CAP_KWARGS),
                    "suggest_kwarg": "max_completion_tokens",
                    "why": "reasoning model: max_tokens es ignorado en Chat API; el techo real es max_completion_tokens",
                })
            continue
    return findings, src


def make_patch(path: Path, src: str, findings, cap_value: int) -> str:
    """Unified diff que agrega `kwarg=cap_value` como ÚLTIMO argumento de cada constructor sin cap.
    Inserción basada en AST (robusta a args POSICIONALES, strings con paréntesis, y kwargs previos): el kwarg
    va antes del `)` de cierre del call, NUNCA tras el `(` (eso produciría `Ctor(kwarg=…, "positional")` =
    SyntaxError — codex/Cursor r76). NUNCA escribe el archivo — solo el diff (council 002 P0-2)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return ""
    # map (lineno, constructor) → list of Call nodes, to insert at the exact end of the right call
    by_key = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            by_key.setdefault((node.lineno, call_name(node)), []).append(node)
    lines = src.splitlines(keepends=True)
    new_lines = list(lines)
    edits = []   # (line_index, col, text) — applied right-to-left so columns don't shift
    for f in (f for f in findings if f["kind"] == "missing"):
        cands = by_key.get((f["line"], f["constructor"]), [])
        if len(cands) != 1:
            continue   # 0 or >1 matching calls on the line → ambiguous, skip (the finding is still reported)
        call = cands[0]
        if call.end_lineno != call.lineno or call.end_col_offset is None:
            continue   # multi-line call → skip (conservative)
        i = call.lineno - 1
        close = call.end_col_offset - 1   # column of the closing ')'
        had_args = bool(call.args) or bool(call.keywords)
        sep = ", " if had_args else ""
        edits.append((i, close, f"{sep}{f['suggest_kwarg']}={cap_value}"))
    # apply right-to-left (highest column on a line first) so earlier insertions don't shift later columns
    for i, col, text in sorted(edits, key=lambda e: (e[0], -e[1])):
        line = new_lines[i]
        new_lines[i] = line[:col] + text + line[col:]
    if new_lines == lines:
        return ""
    rel = str(path)
    return "".join(difflib.unified_diff(lines, new_lines,
                                        fromfile=f"a/{rel}", tofile=f"b/{rel}"))


def scan_path(root: Path, max_files: int = 5000):
    """Escanea un árbol; devuelve (findings_por_archivo, n_escaneados)."""
    out = {}
    n = 0
    for py in sorted(root.rglob("*.py")):
        if any(part in EXCLUDE_DIRS for part in py.parts):
            continue
        # NO seguir symlinks — un repo hostil podría apuntar fuera del árbol escaneado
        # (path traversal del scanner). Mismo guard que cli._find_units y pack.build_tarball.
        if py.is_symlink() or any(p.is_symlink() for p in py.parents
                                  if root in p.parents or p == root):
            continue
        n += 1
        if n > max_files:
            break
        findings, src = scan_file(py)
        if findings:
            out[py] = (findings, src)
    return out, min(n, max_files)
