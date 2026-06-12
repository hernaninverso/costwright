"""gasket caps — detección de constructores LLM sin cap de tokens + sugerencia por provider.

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
        tree = ast.parse(src)
    except (SyntaxError, OSError):
        return [], None
    findings = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = call_name(node)
        if name not in PROVIDER_CAPS:
            continue
        kwargs_present = {k.arg for k in node.keywords if k.arg}
        provider, kwarg, note = PROVIDER_CAPS[name]
        # detección best-effort de reasoning model por el kwarg `model` (audit-3 gpt-5.5 P0):
        # en Chat API los o-series/GPT-5 ignoran max_tokens; el cap real es max_completion_tokens
        model_val = next((k.value.value for k in node.keywords
                          if k.arg == "model" and isinstance(k.value, ast.Constant)
                          and isinstance(k.value.value, str)), "")
        reasoning = any(model_val.startswith(p) for p in
                        ("o1", "o3", "o4", "gpt-5")) if model_val else False
        # SOLO Chat-API constructors (audit-3 R2 gpt-5.5): el constructor `OpenAI` es
        # Responses API y su cap correcto sigue siendo max_output_tokens, reasoning o no
        if name in ("ChatOpenAI", "AzureChatOpenAI") and reasoning:
            kwarg = "max_completion_tokens"
            note = "reasoning model en Chat API: max_tokens es IGNORADO; usar max_completion_tokens"
        if kwargs_present & CAP_KWARGS:
            # tiene algún cap — chequear degradaciones conocidas (§3.2)
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
            elif name in ("ChatOpenAI", "AzureChatOpenAI") and reasoning and "max_completion_tokens" not in kwargs_present:
                findings.append({
                    "kind": "degraded", "constructor": name, "provider": provider,
                    "line": node.lineno, "have": sorted(kwargs_present & CAP_KWARGS),
                    "suggest_kwarg": "max_completion_tokens",
                    "why": "reasoning model: max_tokens es ignorado en Chat API; el techo real es max_completion_tokens",
                })
            continue
        findings.append({
            "kind": "missing", "constructor": name, "provider": provider,
            "line": node.lineno, "suggest_kwarg": kwarg, "note": note,
        })
    return findings, src


def make_patch(path: Path, src: str, findings, cap_value: int) -> str:
    """Unified diff que agrega `kwarg=cap_value` a cada constructor sin cap.
    Edición textual mínima: insertar el kwarg tras el paréntesis de apertura del call.
    NUNCA escribe el archivo — solo el diff (council 002 P0-2)."""
    lines = src.splitlines(keepends=True)
    new_lines = list(lines)
    # de abajo hacia arriba para no correr line numbers
    for f in sorted((f for f in findings if f["kind"] == "missing"),
                    key=lambda x: -x["line"]):
        i = f["line"] - 1
        if i >= len(new_lines):
            continue
        line = new_lines[i]
        ctor = f["constructor"]
        # audit-3 (gemini P0): si hay >1 ocurrencia del constructor en la línea, NO parchear
        # (la inserción textual no sabe cuál es cuál) — conservador, el hallazgo igual se reporta
        if line.count(ctor + "(") != 1:
            continue
        idx = line.find(ctor + "(")
        if idx < 0:
            continue  # constructor multilínea: skip (conservador)
        insert_at = idx + len(ctor) + 1
        rest = line[insert_at:]
        sep = "" if rest.lstrip().startswith(")") else ", "
        new_lines[i] = line[:insert_at] + f"{f['suggest_kwarg']}={cap_value}{sep}" + rest
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
