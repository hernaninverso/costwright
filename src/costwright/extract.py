"""F2a — extract: por graph unit, AST → ExtractionResult.

Emite: nodos, edges (static/conditional-literal/conditional-fn/dynamic-goto/send), ciclos,
bounds con fuente (D2/D8), caps de tokens, features no soportadas. 100% estático (D3).
"""
import ast
from pathlib import Path

# D8 — tabla verificada 2026-06-12 (fuentes en spec.md)
DEFAULTS = {
    "langgraph_recursion_limit_modern": 1000,   # >=1.0.6
    "langgraph_recursion_limit_legacy": 25,     # <1.0.6
    "crewai_max_iter": 20,
    "agents_sdk_max_turns": 10,
}
CAP_KWARGS = {"max_tokens", "max_output_tokens", "max_completion_tokens", "budget_tokens",
              "max_tokens_to_sample", "maxOutputTokens"}

def call_name(n: ast.Call) -> str:
    f = n.func
    if isinstance(f, ast.Name): return f.id
    if isinstance(f, ast.Attribute):
        parts = []
        cur = f
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr); cur = cur.value
        if isinstance(cur, ast.Name): parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""

def const_of(node):
    if isinstance(node, ast.Constant): return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub) and isinstance(node.operand, ast.Constant):
        return -node.operand.value
    return None

class Extractor(ast.NodeVisitor):
    def __init__(s, src):
        s.src = src
        s.nodes = []          # (name|None, lineno)
        s.edges = []          # dicts {kind, src, dst, line}
        s.bounds = []         # {param, value|None, source, line}
        s.caps = []           # {kwarg, value|None, line}
        s.features = []       # {feature, line} no-soportadas / señales
        s.llm_calls = 0       # heurística: invocaciones a modelos dentro del archivo
        s.while_true_invokes = []
        s._in_while_true = 0
        s.compiled_vars = set()   # vars bound to X.compile()  — aliased subgraphs (audit-3 codex)
        s.pregel_vars = set()     # vars bound to Pregel(...)   — unresolvable subgraphs

    def visit_While(s, n):
        is_true = isinstance(n.test, ast.Constant) and n.test.value is True
        # REPL interactivo: while True con input() en el cuerpo — el humano es el loop,
        # NO es un driver runaway autónomo (revisión D5: u082/u229 eran chat-REPLs)
        if is_true:
            body_src = ast.dump(n)
            if "id='input'" in body_src or 'id="input"' in body_src:
                s.features.append({"feature": "interactive-repl", "line": n.lineno})
                s.generic_visit(n); return
        if is_true: s._in_while_true += 1
        s.generic_visit(n)
        if is_true: s._in_while_true -= 1

    def visit_Call(s, n):
        name = call_name(n)
        last = name.split(".")[-1]

        if last == "add_node":
            arg0 = n.args[0] if n.args else None
            nname = const_of(arg0) if arg0 is not None else None
            if not isinstance(nname, str) and len(n.args) == 1:
                # LangGraph permite add_node(fn) — 1 SOLO arg: el nombre se infiere de
                # fn.__name__ → nodo nombrado estáticamente (rev D5). Con 2 args, arg0 variable
                # = NOMBRE dinámico (string en runtime) → queda None (dinámico).
                if isinstance(arg0, ast.Name): nname = arg0.id
                elif isinstance(arg0, ast.Attribute): nname = arg0.attr
            s.nodes.append((nname if isinstance(nname, str) else None, n.lineno))
            # subgraph como nodo: add_node(name, X.compile()) — el handler es OTRO grafo;
            # el costo del nodo no es 1 call (rev D5: u139). delegate() lo cubriría; el
            # harness v1 no lo implementa → feature medida.
            for a in list(n.args[1:]) + [k.value for k in n.keywords]:
                aliased = isinstance(a, ast.Name) and (a.id in s.compiled_vars or a.id in s.pregel_vars)
                if (isinstance(a, ast.Call) and call_name(a).split(".")[-1] == "compile") or aliased:
                    # subgraph node: inline X.compile() OR a var bound to .compile()/Pregel (audit-3 codex) —
                    # an aliased compiled subgraph passed by variable must NOT certify as a normal node.
                    s.features.append({"feature": "subgraph-node", "line": n.lineno})
        elif last == "add_edge":
            a = const_or_endref(n.args[0]) if len(n.args) > 0 else None
            b = const_or_endref(n.args[1]) if len(n.args) > 1 else None
            s.edges.append({"kind": "static", "src": a, "dst": b, "line": n.lineno})
        elif last == "add_conditional_edges":
            # dst enumerable si hay dict literal en args/kwargs
            mapping = None
            for x in list(n.args) + [k.value for k in n.keywords]:
                if isinstance(x, ast.Dict): mapping = x
            if mapping is not None:
                dsts = [const_or_endref(v) for v in mapping.values]
                s.edges.append({"kind": "conditional-literal", "src": None, "dsts": dsts, "line": n.lineno})
            else:
                s.edges.append({"kind": "conditional-fn", "src": None, "dsts": None, "line": n.lineno})
        elif last == "Send":
            s.features.append({"feature": "send-fanout", "line": n.lineno})
        elif last == "Command":
            goto = next((k.value for k in n.keywords if k.arg == "goto"), None)
            if goto is not None and const_of(goto) is None and not isinstance(goto, ast.List):
                s.features.append({"feature": "dynamic-goto", "line": n.lineno})
            elif goto is not None:
                s.edges.append({"kind": "static", "src": None, "dst": const_of(goto), "line": n.lineno})
        elif (last == "interrupt" or last == "NodeInterrupt"
              or name.endswith("interrupt_before") or name.endswith("interrupt_after")):
            s.features.append({"feature": "interrupt-human-in-loop", "line": n.lineno})
        elif last in ("invoke", "stream", "ainvoke", "astream", "batch", "abatch", "kickoff",
                      "run", "run_sync", "run_streamed"):
            s._scan_invoke(n)
            if s._in_while_true: s.while_true_invokes.append(n.lineno)
        elif last == "compile":
            for k in n.keywords:
                if k.arg in ("interrupt_before", "interrupt_after"):
                    s.features.append({"feature": "interrupt-human-in-loop", "line": n.lineno})
        elif last in ("Agent",):
            mi = next((k for k in n.keywords if k.arg == "max_iter"), None)
            if mi is not None:
                s.bounds.append({"param": "max_iter", "value": const_of(mi.value),
                                 "source": "explicit", "line": n.lineno})
            # CrewAI Agent sin max_iter → default 20 (lo decide el mapper por-kind)
        elif last == "Crew":
            proc = next((k for k in n.keywords if k.arg == "process"), None)
            if proc is not None and "hierarchical" in ast.dump(proc.value):
                s.features.append({"feature": "hierarchical-manager", "line": n.lineno})

        # caps de tokens en cualquier call (constructores de modelos, llamadas)
        for k in n.keywords:
            if k.arg in CAP_KWARGS:
                s.caps.append({"kwarg": k.arg, "value": const_of(k.value), "line": n.lineno})
        # heurística de llamadas a LLM
        if last in ("ChatOpenAI", "ChatAnthropic", "ChatGoogleGenerativeAI", "ChatBedrock",
                    "AzureChatOpenAI", "ChatVertexAI", "OpenAI", "Anthropic", "LLM",
                    "init_chat_model", "ChatGroq", "ChatMistralAI", "ChatOllama"):
            s.llm_calls += 1
        s.generic_visit(n)

    def _scan_invoke(s, n):
        """Busca recursion_limit / max_turns en el config del call-site (D2)."""
        for k in n.keywords:
            if k.arg == "max_turns":
                # distinguir None LITERAL (desactivación deliberada) de expresión no-constante
                # (bound real irrecuperable estáticamente) — revisión D5: u087 era settings.max_turns
                none_lit = isinstance(k.value, ast.Constant) and k.value.value is None
                s.bounds.append({"param": "max_turns", "value": const_of(k.value),
                                 "none_literal": none_lit,
                                 "source": "explicit", "line": n.lineno})
            if k.arg == "config" and isinstance(k.value, ast.Dict):
                for kk, vv in zip(k.value.keys, k.value.values):
                    if const_of(kk) == "recursion_limit":
                        s.bounds.append({"param": "recursion_limit", "value": const_of(vv),
                                         "source": "explicit", "line": n.lineno})

    def visit_Dict(s, n):
        # config dicts armados aparte: {"recursion_limit": N, ...}
        for kk, vv in zip(n.keys, n.values):
            if const_of(kk) == "recursion_limit":
                s.bounds.append({"param": "recursion_limit", "value": const_of(vv),
                                 "source": "explicit", "line": n.lineno})
        s.generic_visit(n)

def const_or_endref(node):
    v = const_of(node)
    if v is not None: return v
    if isinstance(node, ast.Name) and node.id in ("START", "END"): return node.id
    if isinstance(node, ast.Attribute) and node.attr in ("START", "END"): return node.attr
    return None

def find_cycles(nodes, edges):
    """DFS sobre edges con dst resuelto. Conservador: edges no resueltos no crean ciclo
    (el mapper los trata como dynamic)."""
    g = {}
    for e in edges:
        if e["kind"] == "static" and e.get("src") and e.get("dst") and e["dst"] != "END":
            g.setdefault(e["src"], set()).add(e["dst"])
        elif e["kind"] == "conditional-literal" and e.get("dsts"):
            # src desconocido en muchos casos; si no hay src, no podemos cerrar ciclo → skip
            if e.get("src"):
                for d in e["dsts"]:
                    if d and d != "END": g.setdefault(e["src"], set()).add(d)
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {u: WHITE for u in g}
    cyc = False
    def dfs(u):
        nonlocal cyc
        color[u] = GRAY
        for v in g.get(u, ()):
            if color.get(v, WHITE) == GRAY: cyc = True
            elif color.get(v, WHITE) == WHITE: dfs(v)
        color[u] = BLACK
    for u in list(g):
        if color[u] == WHITE: dfs(u)
    return cyc

def extract_unit(unit_dir: Path, meta: dict) -> dict:
    f = unit_dir / meta["file"]
    src = f.read_text(encoding="utf-8", errors="ignore")
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return {"unit_id": meta["unit_id"], "status": "extractor-failure", "reason": "syntax"}
    ex = Extractor(src)
    # prepass (order-independent): a name bound to an expression CONTAINING a `.compile()` (or to a Pregel)
    # via ANY binding form — assign / annotated / walrus / for-target / with-as, incl. tuple/list targets —
    # is treated as a possible compiled subgraph, so an aliased subgraph reaching add_node is flagged
    # subgraph-node (→ compose, which resolves a clean `c = g.compile()` alias or FAILS CLOSED for an opaque
    # binding) instead of silently counted as one normal node by the flat path (audit-3 codex r26/r27/r28).
    def _has_compile(node):
        return any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "compile"
                   for x in ast.walk(node))

    def _names(t):
        if isinstance(t, ast.Name): return [t.id]
        if isinstance(t, ast.Starred): return _names(t.value)
        if isinstance(t, (ast.Tuple, ast.List)): return [n for e in t.elts for n in _names(e)]
        return []

    for nd in ast.walk(tree):
        binds = []   # (target_node, source_expr)
        if isinstance(nd, ast.Assign):
            binds = [(t, nd.value) for t in nd.targets]
        elif isinstance(nd, (ast.AnnAssign, ast.NamedExpr)) and nd.value is not None:
            binds = [(nd.target, nd.value)]
        elif isinstance(nd, (ast.For, ast.AsyncFor)):
            binds = [(nd.target, nd.iter)]
        elif isinstance(nd, (ast.With, ast.AsyncWith)):
            binds = [(it.optional_vars, it.context_expr) for it in nd.items if it.optional_vars is not None]
        for tgt, src in binds:
            if _has_compile(src):
                for nm in _names(tgt):
                    ex.compiled_vars.add(nm)
            if isinstance(src, ast.Call) and call_name(src).split(".")[-1] == "Pregel":
                for nm in _names(tgt):
                    ex.pregel_vars.add(nm)
    ex.visit(tree)
    has_cycle = find_cycles(ex.nodes, ex.edges)
    # ciclo "implícito" típico LangGraph: conditional edges que vuelven a un nodo previo —
    # si hay conditional-literal cuyos dsts incluyen un nodo definido, lo tratamos como posible ciclo
    cond_back = any(e["kind"] == "conditional-literal" and e.get("dsts") and
                    any(d for d in e["dsts"] if d and d != "END") for e in ex.edges)
    out = {
        "unit_id": meta["unit_id"], "kind": meta["kind"], "status": "ok",
        "n_nodes": len(ex.nodes), "n_nodes_named": sum(1 for n, _ in ex.nodes if n),
        "n_nodes_dynamic": sum(1 for n, _ in ex.nodes if n is None),
        "edges": ex.edges, "has_static_cycle": has_cycle, "cond_may_cycle": cond_back,
        "bounds": ex.bounds, "caps": ex.caps, "features": ex.features,
        "llm_constructors": ex.llm_calls, "while_true_invokes": ex.while_true_invokes,
    }
    # feature 005: only when a subgraph-node is present, run the per-graph analysis for composition
    # (lazy import avoids a circular dependency; the flat path above is untouched for normal files).
    if any(f["feature"] == "subgraph-node" for f in ex.features):
        from costwright.subgraph import analyze
        out["subgraph_analysis"] = analyze(tree)
    return out
