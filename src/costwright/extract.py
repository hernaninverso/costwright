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

def contains_compile(node):
    """True if the AST subtree contains any `<...>.compile()` call — used to flag a possible compiled
    subgraph wherever it appears (alias binding, or an add_node arg like `identity(g.compile())`)."""
    return any(isinstance(x, ast.Call) and isinstance(x.func, ast.Attribute) and x.func.attr == "compile"
               for x in ast.walk(node))


def literal_reflective_attr(func):
    """If `func` is a CONSTANT-LITERAL reflective name lookup — `getattr(_, "X")`, `vars(_)["X"]`,
    `_.__dict__["X"]`, `globals()["X"]`, `locals()["X"]` — return the looked-up name string X, else None.
    Used to catch a Send/Command/interrupt invoked DIRECTLY through such an expression, e.g.
    `getattr(lgtypes, "Send")("sub", {})` (codex/Cursor r47), which binds no name so alias propagation
    never sees it. Precise: `getattr(llm, "invoke")` returns "invoke", not a construct ⇒ no false block."""
    if (isinstance(func, ast.Call) and isinstance(func.func, ast.Name) and func.func.id == "getattr"
            and len(func.args) >= 2 and isinstance(func.args[1], ast.Constant)
            and isinstance(func.args[1].value, str)):
        return func.args[1].value
    if isinstance(func, ast.Subscript) and isinstance(func.slice, ast.Constant) and isinstance(func.slice.value, str):
        base = func.value
        if ((isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
                and base.func.id in {"vars", "globals", "locals"})
                or (isinstance(base, ast.Attribute) and base.attr == "__dict__")):
            return func.slice.value
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
        s.send_aliases = {"Send"}        # names that resolve to langgraph Send (incl. import aliases, r37)
        s.command_aliases = {"Command"}  # names that resolve to langgraph Command (incl. import aliases)
        s.interrupt_aliases = {"interrupt", "NodeInterrupt"}  # names that resolve to interrupt (r40)

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
            # harness v1 no lo implementa → feature medida. Escaneamos TODOS los args posicionales
            # (no solo args[1:]) porque LangGraph permite `add_node(inner.compile())` de 1 arg, con el
            # subgrafo compilado en arg0 (el nombre se infiere del runnable) — codex r45. Un arg0 que
            # es un string-nombre normal nunca contiene .compile() → sin falso positivo.
            for a in list(n.args) + [k.value for k in n.keywords]:
                refs_compiled = any(isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                                    and (x.id in s.compiled_vars or x.id in s.pregel_vars)
                                    for x in ast.walk(a))
                if refs_compiled or contains_compile(a):
                    # subgraph node: the arg CONTAINS a .compile() (inline `g.compile()` / wrapped
                    # `identity(g.compile())` — Cursor r29) OR Load-references a compiled var ANYWHERE — a bare
                    # alias, or an attribute `holder.c` (Cursor r34). Must NOT certify as a normal node; routes
                    # to compose (resolves a clean alias, else fails closed).
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
        # a construct invoked DIRECTLY through a literal reflective access — `getattr(lgtypes,"Send")(...)`,
        # `globals()["S"](...)` — binds no name, so the callee itself resolves it (codex/Cursor r47). The
        # looked-up name hits send/command/interrupt aliases just like a plain `last` would ("Send" is in
        # send_aliases as the base; propagated aliases like "S" are there too).
        elif last in s.send_aliases or literal_reflective_attr(n.func) in s.send_aliases:
            s.features.append({"feature": "send-fanout", "line": n.lineno})
        elif last in s.command_aliases or literal_reflective_attr(n.func) in s.command_aliases:
            goto = next((k.value for k in n.keywords if k.arg == "goto"), None)
            if goto is not None and const_of(goto) is None and not isinstance(goto, ast.List):
                s.features.append({"feature": "dynamic-goto", "line": n.lineno})
            elif goto is not None:
                s.edges.append({"kind": "static", "src": None, "dst": const_of(goto), "line": n.lineno})
        elif (last in s.interrupt_aliases or literal_reflective_attr(n.func) in s.interrupt_aliases
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
    # import aliases for Send / Command so `from langgraph.types import Send as S` doesn't bypass the
    # send-fanout / dynamic-goto blocking (Cursor r37). Order-independent (imports precede use in valid code,
    # but the walk is robust regardless).
    for nd in ast.walk(tree):
        if isinstance(nd, ast.ImportFrom):
            for a in nd.names:
                if a.name == "Send":
                    ex.send_aliases.add(a.asname or a.name)
                elif a.name == "Command":
                    ex.command_aliases.add(a.asname or a.name)
                elif a.name in ("interrupt", "NodeInterrupt"):
                    ex.interrupt_aliases.add(a.asname or a.name)
    # prepass (order-independent): a name bound to an expression CONTAINING a `.compile()` (or to a Pregel)
    # via ANY binding form — assign / annotated / walrus / for-target / with-as, incl. tuple/list targets —
    # is treated as a possible compiled subgraph, so an aliased subgraph reaching add_node is flagged
    # subgraph-node (→ compose, which resolves a clean `c = g.compile()` alias or FAILS CLOSED for an opaque
    # binding) instead of silently counted as one normal node by the flat path (audit-3 codex r26/r27/r28).
    def _names(t):
        if isinstance(t, ast.Name): return [t.id]
        if isinstance(t, ast.Starred): return _names(t.value)
        if isinstance(t, (ast.Tuple, ast.List)): return [n for e in t.elts for n in _names(e)]
        return []

    def _container_base(t):
        # root Name of a Subscript/Attribute assignment-target chain: `d["k"]`→"d", `obj.sub`→"obj",
        # `reg[i].x`→"reg". Used to TAINT the container when a compiled subgraph is stashed in it (codex r55).
        while isinstance(t, (ast.Subscript, ast.Attribute)):
            t = t.value
        return t.id if isinstance(t, ast.Name) else None

    def _match_names(p):   # names captured by a match-case pattern (Cursor r35)
        out = []
        if isinstance(p, ast.MatchAs):
            if p.name: out.append(p.name)
            if p.pattern: out.extend(_match_names(p.pattern))
        elif isinstance(p, ast.MatchStar):
            if p.name: out.append(p.name)
        elif isinstance(p, ast.MatchMapping):
            if p.rest: out.append(p.rest)
            for sub in p.patterns: out.extend(_match_names(sub))
        elif isinstance(p, ast.MatchSequence):
            for sub in p.patterns: out.extend(_match_names(sub))
        elif isinstance(p, ast.MatchClass):
            for sub in list(p.patterns) + list(p.kwd_patterns): out.extend(_match_names(sub))
        elif isinstance(p, ast.MatchOr):
            for sub in p.patterns: out.extend(_match_names(sub))
        return out

    all_binds = []   # (target_names, value_expr) over every binding form
    container_binds = []   # (base_name, value) when a subgraph is stashed in a container/attr (codex r55)
    for nd in ast.walk(tree):
        bs = []
        if isinstance(nd, ast.Assign):
            bs = [(t, nd.value) for t in nd.targets]
        elif isinstance(nd, (ast.AnnAssign, ast.NamedExpr)) and nd.value is not None:
            bs = [(nd.target, nd.value)]
        elif isinstance(nd, (ast.For, ast.AsyncFor)):
            bs = [(nd.target, nd.iter)]
        elif isinstance(nd, (ast.With, ast.AsyncWith)):
            bs = [(it.optional_vars, it.context_expr) for it in nd.items if it.optional_vars is not None]
        elif isinstance(nd, ast.Match):
            # `match <subject>: case <pat>:` binds the case's captured names to (part of) the subject; if the
            # subject is a compiled graph, those captures could be it (Cursor r35).
            names = [n for c in nd.cases for n in _match_names(c.pattern)]
            if names:
                all_binds.append((names, nd.subject))
        elif isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            # a parameter DEFAULT `def f(x=inner.compile())` binds x to the default's value (Cursor r36).
            ar = nd.args
            pos = list(ar.posonlyargs) + list(ar.args)
            for arg, default in zip(pos[len(pos) - len(ar.defaults):], ar.defaults):
                all_binds.append(([arg.arg], default))
            for arg, default in zip(ar.kwonlyargs, ar.kw_defaults):
                if default is not None:
                    all_binds.append(([arg.arg], default))
        for tgt, src in bs:
            all_binds.append((_names(tgt), src))
            if tgt is not None and not _names(tgt):
                base = _container_base(tgt)   # Subscript/Attribute target → taint the container (codex r55)
                if base is not None:
                    container_binds.append((base, src))
            if isinstance(src, ast.Call) and call_name(src).split(".")[-1] == "Pregel":
                for nm in _names(tgt):
                    ex.pregel_vars.add(nm)

    def _loads_any(value, names):
        return any(isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in names
                   for x in ast.walk(value))

    def _refs_construct(value, name_set, attr_set):
        # value references a langgraph construct either by a known alias Name, an attribute `mod.Send`
        # (Cursor r41), OR a CONSTANT-LITERAL reflective access that statically resolves to a construct name
        # (codex/Cursor r42/r43): `getattr(mod, "Send")`, `vars(mod)["Send"]`, `mod.__dict__["Send"]`,
        # `globals()["Send"]`. The literal attr/key is right there in the AST, so leaving it unhandled would
        # understate a fan-out reachable through reflection. (Non-literal `getattr(mod, var)` / `eval` /
        # monkeypatching stays unobservable → documented out of scope, voids the certificate.)
        for x in ast.walk(value):
            if isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in name_set:
                return True
            if isinstance(x, ast.Attribute) and x.attr in attr_set:
                return True
            if (isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "getattr"
                    and len(x.args) >= 2 and isinstance(x.args[1], ast.Constant)
                    and x.args[1].value in attr_set):
                return True
            if isinstance(x, ast.Subscript) and isinstance(x.slice, ast.Constant) and x.slice.value in attr_set:
                base = x.value
                if ((isinstance(base, ast.Call) and isinstance(base.func, ast.Name)
                        and base.func.id in {"vars", "globals", "locals"})
                        or (isinstance(base, ast.Attribute) and base.attr == "__dict__")):
                    return True
        return False

    # A name is a POSSIBLE compiled subgraph if its binding value contains a `.compile()` OR Load-references
    # another compiled var — covering ALL alias chains in one fixpoint: `alias = compiled`, `(a,) = (c,)`,
    # `a = c[0]`, `a = wrap(c)`, … (Cursor r31/r33). A flagged name reaching add_node routes to compose
    # (resolves a clean `c = g.compile()`, else fails closed) — never the flat undercount. Over-flagging a
    # non-graph derivation only fails closed (safe).
    changed = True
    while changed:
        changed = False
        for names, value in all_binds:
            if contains_compile(value) or _loads_any(value, ex.compiled_vars):
                for nm in names:
                    if nm not in ex.compiled_vars:
                        ex.compiled_vars.add(nm)
                        changed = True
        # a compiled subgraph stashed in a CONTAINER/attribute (`d["k"] = inner.compile()`, `reg.sub = c`) taints
        # the base name: a later `add_node("s", d["k"])` Load-references `d` ∈ compiled_vars → flagged as a possible
        # subgraph node → the analyzer can't attribute the subscript inner → the completeness guard fails closed.
        # Over-tainting a container only makes its uses fail closed (safe). (codex r55)
        for base, value in container_binds:
            if (contains_compile(value) or _loads_any(value, ex.compiled_vars)) and base not in ex.compiled_vars:
                ex.compiled_vars.add(base)
                changed = True
        for names, value in all_binds:
            # aliasing of Send/Command through ANY binding shape (`S = Send`, `S, = (Send,)`, `S = (Send,)[0]`,
            # `T = S`, …) — propagate so the fan-out / dynamic-goto blocking can't be bypassed (Cursor r38/r39).
            # Over-flagging only makes a call block (fail closed), which is the safe direction.
            if _refs_construct(value, ex.send_aliases, {"Send"}):
                for nm in names:
                    if nm not in ex.send_aliases:
                        ex.send_aliases.add(nm)
                        changed = True
            if _refs_construct(value, ex.command_aliases, {"Command"}):
                for nm in names:
                    if nm not in ex.command_aliases:
                        ex.command_aliases.add(nm)
                        changed = True
            if _refs_construct(value, ex.interrupt_aliases, {"interrupt", "NodeInterrupt"}):
                for nm in names:
                    if nm not in ex.interrupt_aliases:
                        ex.interrupt_aliases.add(nm)
                        changed = True
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
