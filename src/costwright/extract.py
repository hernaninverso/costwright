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
        s._loop_depth = 0   # >0 inside any For/While/comprehension — add_node here builds N runtime nodes (r68)
        # CrewAI cost model (codex r86 + r86b): a SEQUENTIAL crew runs each task with up to its agent's max_iter
        # iterations ⇒ per-run worst case = n_tasks × max(agent budget). One entry per Agent() ctor (its max_iter
        # budget: int | "default"=20 | None=unrecoverable), and one entry per Crew() ctor (its literal task /
        # agent counts, or None when dynamic) — the mapper combines them and fails closed on any None.
        s.crewai_agent_budgets = []   # int | "default" | None, one per Agent() constructor
        s.crewai_crews = []           # {"tasks": int|None, "agents": int|None}, one per Crew() constructor
        s.agent_vars = set()          # Names bound to an Agent() ctor — a Task referencing an unknown agent fails closed
        s.compiled_vars = set()   # vars bound to X.compile()  — aliased subgraphs (audit-3 codex)
        s.compiled_factory_names = set()  # function/method NAMES that return a compiled subgraph (r56/r58)
        s.addnode_aliases = set()  # names bound to `g.add_node` (bound-method alias / partial) — r63
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
        s._loop_depth += 1
        s.generic_visit(n)
        s._loop_depth -= 1
        if is_true: s._in_while_true -= 1

    def visit_For(s, n):
        s._loop_depth += 1
        s.generic_visit(n)
        s._loop_depth -= 1

    visit_AsyncFor = visit_For

    def _visit_comp(s, n):
        s._loop_depth += 1
        s.generic_visit(n)
        s._loop_depth -= 1

    visit_ListComp = _visit_comp
    visit_SetComp = _visit_comp
    visit_DictComp = _visit_comp
    visit_GeneratorExp = _visit_comp

    def visit_Call(s, n):
        name = call_name(n)
        last = name.split(".")[-1]

        if last == "add_node" or (isinstance(n.func, ast.Name) and n.func.id in s.addnode_aliases):
            # an add_node inside a loop/comprehension builds N runtime nodes from ONE textual site → the static
            # node count undercounts → fail closed (codex r68; mirrors the subgraph path's loop guard).
            if s._loop_depth > 0:
                s.features.append({"feature": "node-in-loop", "line": n.lineno})
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
                # an ATTRIBUTE whose name resolves to a compiled subgraph — a factory method `Factory.make()` /
                # `obj.make()` (r58) OR a class/instance attribute holding a subgraph `C.sub` (r61) — matched by
                # the attribute NAME (called or not). Bare-name `make()`/`sub` is already in compiled_vars.
                attr_subgraph = any(isinstance(x, ast.Attribute) and x.attr in s.compiled_factory_names
                                    for x in ast.walk(a))
                if refs_compiled or contains_compile(a) or attr_subgraph:
                    # subgraph node: the arg CONTAINS a .compile() (inline `g.compile()` / wrapped
                    # `identity(g.compile())` — Cursor r29) OR Load-references a compiled var ANYWHERE — a bare
                    # alias, or an attribute `holder.c` (Cursor r34). Must NOT certify as a normal node; routes
                    # to compose (resolves a clean alias, else fails closed).
                    s.features.append({"feature": "subgraph-node", "line": n.lineno})
            # per-node RetryPolicy / error_handler / **kwargs re-execute or add nodes beyond the ≤1-per-
            # super-step model — the FLAT path must fail closed too, not just composition (codex r67, mirrors
            # subgraph.py _mark_unmodeled). A RetryPolicy(max_attempts=k) runs the node up to k× per super-step.
            for k in n.keywords:
                if k.arg is None or k.arg in ("retry", "retry_policy", "error_handler"):
                    s.features.append({"feature": "node-unmodeled-retry", "line": n.lineno})
        elif last == "set_node_defaults":
            # graph-wide RetryPolicy / error_handler — same unmodeled re-execution (codex r67).
            for k in n.keywords:
                if k.arg is None or k.arg in ("retry", "retry_policy", "error_handler"):
                    s.features.append({"feature": "node-unmodeled-retry", "line": n.lineno})
        elif last == "add_sequence":
            if s._loop_depth > 0:
                s.features.append({"feature": "node-in-loop", "line": n.lineno})
            # `g.add_sequence([(name, action), ...])` adds ONE node per element — the FLAT path must count them
            # all or it understates (codex r65). A static list/tuple literal is counted element-by-element; a
            # non-literal sequence (unknown length) fails closed; an element carrying a compiled subgraph routes
            # to compose / fail-closed like an add_node subgraph arg.
            seq = n.args[0] if n.args else (n.keywords[0].value if n.keywords else None)
            if isinstance(seq, (ast.List, ast.Tuple)):
                for elt in seq.elts:
                    if isinstance(elt, ast.Starred):
                        s.features.append({"feature": "add-sequence-dynamic", "line": n.lineno})
                        continue
                    nm = const_of(elt.elts[0]) if isinstance(elt, (ast.Tuple, ast.List)) and elt.elts else None
                    s.nodes.append((nm if isinstance(nm, str) else None, n.lineno))
                    # PARITY with the add_node subgraph detection (Cursor r84): an element action that is a
                    # compiled subgraph must route to compose / fail-closed — whether it carries an inline
                    # `.compile()`, Load-references a compiled var, OR is a factory-method/attribute whose name
                    # is in compiled_factory_names (`Factory.make()` / `obj.sub`). add_sequence previously
                    # checked only the first two ⇒ a factory-method subgraph silently certified an understated
                    # flat bound.
                    refs_compiled = any(isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load)
                                        and (x.id in s.compiled_vars or x.id in s.pregel_vars)
                                        for x in ast.walk(elt))
                    attr_subgraph = any(isinstance(x, ast.Attribute) and x.attr in s.compiled_factory_names
                                        for x in ast.walk(elt))
                    if contains_compile(elt) or refs_compiled or attr_subgraph:
                        s.features.append({"feature": "subgraph-node", "line": n.lineno})
            else:
                s.features.append({"feature": "add-sequence-dynamic", "line": n.lineno})
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
            # allow_delegation=True gives the agent "delegate/ask coworker" tools — using one invokes ANOTHER
            # agent's executor (its own max_iter loop) INSIDE this task's loop ⇒ a recursive delegation tree that
            # n_tasks × max(budget) does NOT bound (Cursor r86). Fail closed unless it is a constant False.
            deleg = next((k for k in n.keywords if k.arg == "allow_delegation"), None)
            if deleg is not None and not (isinstance(deleg.value, ast.Constant) and deleg.value.value is False):
                s.features.append({"feature": "agent-delegation", "line": n.lineno})
            # a step_callback is ARBITRARY code run after every agent step — it can re-enter (a nested
            # kickoff/loop) and add unbounded activations the n_tasks × max(budget) model does not see (Cursor
            # r95). Fail closed unless it is a constant None (the default = no callback).
            cb = next((k for k in n.keywords if k.arg == "step_callback"), None)
            if cb is not None and not (isinstance(cb.value, ast.Constant) and cb.value.value is None):
                s.features.append({"feature": "crewai-callback", "line": n.lineno})
            mi = next((k for k in n.keywords if k.arg == "max_iter"), None)
            if mi is not None:
                v = const_of(mi.value)
                s.bounds.append({"param": "max_iter", "value": v,
                                 "source": "explicit", "line": n.lineno})
                s.crewai_agent_budgets.append(v)   # int, or None if non-constant (→ unresolved-bound, fail closed)
            elif any(k.arg is None for k in n.keywords):
                # a **kwargs spread on the Agent could carry max_iter (huge or disabling) → unrecoverable; do NOT
                # fall back to the framework default 20 (that would understate) → fail closed (codex/Cursor r80).
                s.bounds.append({"param": "max_iter", "value": None, "source": "explicit", "line": n.lineno})
                s.crewai_agent_budgets.append(None)
            else:
                # CrewAI Agent without max_iter → framework default 20. RECORD it (codex r86): omitting the
                # default for unannotated agents understated a mixed crew (one explicit agent reset the bound to
                # only that agent's budget). The mapper takes max over all agent budgets, defaults included.
                s.crewai_agent_budgets.append("default")
        elif last == "Crew":
            # a hierarchical Crew runs a MANAGER that re-delegates (an unbounded loop) → fail closed. A
            # `manager_agent=` or `manager_llm=` kwarg implies hierarchical coordination (codex/Cursor r80), as
            # does any `process=` that is NOT a confirmed-sequential LITERAL (`Process.sequential` / "sequential")
            # — a hierarchical literal, a VARIABLE (`mode = Process.hierarchical; process=mode` — codex r75), or
            # any computed expression could be the manager loop.
            has_manager = any(k.arg in ("manager_agent", "manager_llm") for k in n.keywords)
            spread = any(k.arg is None for k in n.keywords)   # **cfg could hide process=hierarchical / a manager
            proc = next((k for k in n.keywords if k.arg == "process"), None)
            confirmed_sequential = (proc is not None and isinstance(proc.value, (ast.Attribute, ast.Constant))
                                    and "sequential" in ast.dump(proc.value)
                                    and "hierarchical" not in ast.dump(proc.value))
            if has_manager or spread or (proc is not None and not confirmed_sequential):
                s.features.append({"feature": "hierarchical-manager", "line": n.lineno})
            # step_callback / task_callback are ARBITRARY code run per step/task — they can re-enter (a nested
            # kickoff/loop) and add unbounded activations beyond the n_tasks × max model (Cursor r95). Fail
            # closed unless the value is a constant None (the default = no callback).
            for cbk in n.keywords:
                if cbk.arg in ("step_callback", "task_callback") and not (
                        isinstance(cbk.value, ast.Constant) and cbk.value.value is None):
                    s.features.append({"feature": "crewai-callback", "line": n.lineno})

            # task / agent counts for the per-run model (n_tasks × max agent budget). A clean literal list/tuple
            # gives an exact count; ABSENT ⇒ None; present but non-literal/starred ⇒ "dynamic". The mapper
            # requires a literal task count and fails closed on "dynamic" (run length / agent set not pinned).
            def _lit_count(kw):
                if kw is None:
                    return None        # the kwarg is absent
                if not isinstance(kw.value, (ast.List, ast.Tuple)) \
                        or any(isinstance(e, ast.Starred) for e in kw.value.elts):
                    return "dynamic"   # present but not a clean literal list
                return len(kw.value.elts)
            tasks_kw = next((k for k in n.keywords if k.arg == "tasks"), None)
            agents_kw = next((k for k in n.keywords if k.arg == "agents"), None)
            s.crewai_crews.append({"tasks": _lit_count(tasks_kw), "agents": _lit_count(agents_kw)})
        elif last == "Task":
            # a guardrail RE-RUNS the task on a failed check (up to max_retries, default ≥1) ⇒ a guarded task
            # runs its agent loop (1 + retries)× — the n_tasks × max model does NOT bound that (codex r87). Fail
            # closed on any guardrail.
            if any(k.arg == "guardrail" for k in n.keywords):
                s.features.append({"feature": "crewai-task-retry", "line": n.lineno})
            # a Task whose agent is NOT a visible Agent() ctor (imported / dynamically built / a non-Name expr)
            # could carry a far larger max_iter than the visible agents ⇒ max(visible budgets) understates. Fail
            # closed (Cursor r87). An inline Agent(...) or a Name bound to an Agent() ctor is fine; an absent
            # agent= is covered by max over the visible crew agents.
            ag = next((k for k in n.keywords if k.arg == "agent"), None)
            if ag is not None:
                v = ag.value
                inline = isinstance(v, ast.Call) and (
                    (isinstance(v.func, ast.Name) and v.func.id == "Agent")
                    or (isinstance(v.func, ast.Attribute) and v.func.attr == "Agent"))
                known = isinstance(v, ast.Name) and v.id in s.agent_vars
                if not (inline or known):
                    s.features.append({"feature": "crewai-agent-unknown", "line": n.lineno})

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
        # `.batch([i1, i2, ...])` / `.abatch(...)` runs ONE full graph execution PER input, so the call's total
        # node-activations = N × the per-input ceiling — reporting the per-input limit alone UNDERSTATES the
        # batch call's cost (Cursor r94). A LITERAL input list pins N; a non-literal / starred / absent inputs
        # list is an UNBOUNDED multiplicity → record an unresolved bound so the mapper fails closed.
        method = n.func.attr if isinstance(n.func, ast.Attribute) else None
        if method in ("batch", "abatch"):
            arg0 = n.args[0] if n.args else None
            if isinstance(arg0, (ast.List, ast.Tuple)) and not any(isinstance(e, ast.Starred) for e in arg0.elts):
                bn = len(arg0.elts)
                if bn >= 1:
                    s.bounds.append({"param": "batch_n", "value": bn, "source": "explicit", "line": n.lineno})
            else:
                s.bounds.append({"param": "batch_n", "value": None, "source": "explicit", "line": n.lineno})
        # a **kwargs spread on an invoke/run call is OPAQUE — it could carry a max_turns / recursion_limit that
        # DISABLES the cap (e.g. `Runner.run(a, **{"max_turns": None})`, `app.invoke({}, **opts)`) and the bound
        # would be unrecoverable → record an UNRESOLVED bound so the mapper fails closed (codex/Cursor r79).
        if any(k.arg is None for k in n.keywords):
            s.bounds.append({"param": "invoke-kwargs-spread", "value": None,
                             "source": "explicit", "line": n.lineno})
        for k in n.keywords:
            if k.arg == "max_turns":
                # distinguir None LITERAL (desactivación deliberada) de expresión no-constante
                # (bound real irrecuperable estáticamente) — revisión D5: u087 era settings.max_turns
                none_lit = isinstance(k.value, ast.Constant) and k.value.value is None
                s.bounds.append({"param": "max_turns", "value": const_of(k.value),
                                 "none_literal": none_lit,
                                 "source": "explicit", "line": n.lineno})
        # the config can be the `config=` KWARG or the 2nd POSITIONAL arg of a langgraph invoke-family call
        # (`invoke(input, config)`, `batch(inputs, config)`, `stream(input, config)`, …).
        config_dicts = [k.value for k in n.keywords if k.arg == "config" and isinstance(k.value, ast.Dict)]
        if method in ("invoke", "ainvoke", "stream", "astream", "batch", "abatch") \
                and len(n.args) >= 2 and isinstance(n.args[1], ast.Dict):
            config_dicts.append(n.args[1])
        for cfg in config_dicts:
            # a config dict key that is NOT a constant string — `{'recursion_' + 'limit': 20000}` (a computed
            # constant), `{key_var: ...}`, or a `**spread` (key node is None) — could hide a recursion_limit that
            # const-key matching MISSES → understatement / missed runaway (Cursor r96; the subgraph path already
            # fails closed on this). Record an unresolved bound so the mapper fails closed.
            if any(kk is None or const_of(kk) is None for kk in cfg.keys):
                s.bounds.append({"param": "config-dynamic-key", "value": None,
                                 "source": "explicit", "line": n.lineno})
            for kk, vv in zip(cfg.keys, cfg.values):
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

    # CrewAI: collect Names bound to an Agent() constructor (order-independent). A Task that references an agent
    # we CANNOT see — `Task(agent=imported_heavy)` where imported_heavy is not a visible Agent() ctor — would
    # otherwise be bounded by only the VISIBLE agents' max_iter, but the unseen agent could have a far larger
    # budget (codex/Cursor r87). Such a Task fails closed (crewai-agent-unknown).
    def _is_agent_ctor(v):
        return isinstance(v, ast.Call) and (
            (isinstance(v.func, ast.Name) and v.func.id == "Agent")
            or (isinstance(v.func, ast.Attribute) and v.func.attr == "Agent"))
    for nd in ast.walk(tree):
        _val, _tgts = None, []
        if isinstance(nd, ast.Assign):
            _val, _tgts = nd.value, nd.targets
        elif isinstance(nd, ast.AnnAssign) and nd.value is not None:
            _val, _tgts = nd.value, [nd.target]
        elif isinstance(nd, ast.NamedExpr):
            _val, _tgts = nd.value, [nd.target]
        if _is_agent_ctor(_val):
            for _t in _tgts:
                ex.agent_vars.update(_names(_t))

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

    def _own_returns(fn):
        # Return-statement values that belong to `fn` itself (not to a nested def/lambda). A function that
        # returns a compiled subgraph (`def make(): ... return inner.compile()`) is a subgraph FACTORY: a call
        # `add_node("s", make())` would otherwise be flat-counted (codex/Cursor r56). Collect its returns so the
        # fixpoint can taint the function name into compiled_vars when a return carries a compiled subgraph.
        out, stack = [], list(fn.body)
        while stack:
            x = stack.pop()
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue   # a nested function's returns are ITS own, not fn's
            if isinstance(x, ast.Return) and x.value is not None:
                out.append(x.value)
            if isinstance(x, (ast.Yield, ast.YieldFrom)) and x.value is not None:
                out.append(x.value)   # a generator that yields a compiled subgraph is a factory too (codex r59)
            stack.extend(ast.iter_child_nodes(x))
        return out

    all_binds = []   # (target_names, value_expr) over every binding form
    container_binds = []   # (base_name, value) when a subgraph is stashed in a container/attr (codex r55)
    func_returns = []      # (func_name, [return_value_exprs]) — subgraph factories (codex/Cursor r56)
    func_params = {}       # func_name -> [param_names] (taint params of a fn called with a subgraph — r60)
    name_calls = []        # (callee_name, [arg_values]) for calls to a bare-Name function (r60)
    class_attr_binds = []  # (attr_name, value) for class-body `attr = inner.compile()` (accessed C.attr — r61)
    decorated = []         # (decorated_name, [decorator_exprs]) — `@factory def x` makes x a subgraph (r61)
    method_stash = []      # (base_name, [arg_values]) — `c.append(inner.compile())` stash via method (r57)
    # StateGraph build/run methods: a compiled subgraph as their arg is NOT a "stash into base" (e.g.
    # `outer.add_node("s", inner.compile())` builds the graph), so they don't taint the receiver.
    _GRAPH_METHODS = {"add_node", "add_edge", "add_conditional_edges", "add_sequence", "set_entry_point",
                      "set_finish_point", "set_conditional_entry_point", "compile", "invoke", "ainvoke",
                      "stream", "astream", "batch", "abatch"}
    for nd in ast.walk(tree):
        # a compiled subgraph passed as an ARG to a method call on a bare Name — `lst.append(inner.compile())`,
        # `reg.add(c)`, `d.update({"k": c})` — stashes it INTO that receiver; taint the receiver so a later
        # `add_node("s", lst[0])` Load-references it → flagged → fail closed (codex/Cursor r57). Excludes the
        # graph-building methods (those CONSUME a subgraph to build, not store it).
        if (isinstance(nd, ast.Call) and isinstance(nd.func, ast.Attribute)
                and isinstance(nd.func.value, ast.Name) and nd.func.attr not in _GRAPH_METHODS):
            method_stash.append((nd.func.value.id, list(nd.args) + [k.value for k in nd.keywords]))
        # `setattr(obj, "sub", inner.compile())` is the dynamic form of `obj.sub = inner.compile()` — taint the
        # object (its base Name) when the value carries a compiled subgraph, so a later `getattr(obj, "sub")` /
        # `obj.sub` used as an add_node action Load-references it → flagged → fail closed (codex r59).
        if (isinstance(nd, ast.Call) and isinstance(nd.func, ast.Name) and nd.func.id == "setattr"
                and len(nd.args) >= 3):
            base = _container_base(nd.args[0])
            if base is not None:
                method_stash.append((base, [nd.args[2]]))
        # a call to a bare-Name function — `use(inner.compile())` — passes a compiled subgraph INTO it; record
        # so the fixpoint can taint that function's parameters (a later `add_node("s", sub)` inside it then
        # fails closed). Inter-procedural & conservative (codex/Cursor r60).
        if isinstance(nd, ast.Call) and isinstance(nd.func, ast.Name):
            name_calls.append((nd.func.id, list(nd.args) + [k.value for k in nd.keywords]))
        # a class-body attribute holding a compiled subgraph — `class C: sub = inner.compile()` — is accessed as
        # `C.sub`; record the attr name so the add_node attribute check flags it (r61).
        if isinstance(nd, ast.ClassDef):
            for st in nd.body:
                if isinstance(st, ast.Assign):
                    for t in st.targets:
                        for nm in _names(t):
                            class_attr_binds.append((nm, st.value))
                elif isinstance(st, ast.AnnAssign) and st.value is not None and isinstance(st.target, ast.Name):
                    class_attr_binds.append((st.target.id, st.value))
        # a def/class whose DECORATOR returns a compiled subgraph becomes one (`@deco def node: ...` ⇒
        # node = deco(node)); taint the decorated name (r61).
        if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and nd.decorator_list:
            decorated.append((nd.name, list(nd.decorator_list)))
        bs = []
        if isinstance(nd, ast.Assign):
            bs = [(t, nd.value) for t in nd.targets]
        elif isinstance(nd, ast.AugAssign):
            bs = [(nd.target, nd.value)]   # `subs += [c]` taints subs (codex r59)
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
            if isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef)):
                func_returns.append((nd.name, _own_returns(nd)))   # subgraph factory? (codex/Cursor r56)
                params = [a.arg for a in (list(ar.posonlyargs) + list(ar.args) + list(ar.kwonlyargs))]
                if ar.vararg: params.append(ar.vararg.arg)
                if ar.kwarg: params.append(ar.kwarg.arg)
                func_params[nd.name] = params   # so a call passing a compiled subgraph taints them (r60)
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

    def _is_addnode(v, aliases):
        # value is a bound-method reference to `<graph>.add_node` — directly, via an existing alias, or wrapped
        # in functools.partial — so a call to the bound name is really an add_node call (codex r63).
        if isinstance(v, ast.Attribute) and v.attr == "add_node":
            return True
        if isinstance(v, ast.Name) and v.id in aliases:
            return True
        if isinstance(v, ast.Call) and call_name(v).split(".")[-1] == "partial" and v.args:
            return _is_addnode(v.args[0], aliases)
        return False

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
            if _is_addnode(value, ex.addnode_aliases):
                for nm in names:
                    if nm not in ex.addnode_aliases:
                        ex.addnode_aliases.add(nm)
                        changed = True
        # a compiled subgraph stashed in a CONTAINER/attribute (`d["k"] = inner.compile()`, `reg.sub = c`) taints
        # the base name: a later `add_node("s", d["k"])` Load-references `d` ∈ compiled_vars → flagged as a possible
        # subgraph node → the analyzer can't attribute the subscript inner → the completeness guard fails closed.
        # Over-tainting a container only makes its uses fail closed (safe). (codex r55)
        for base, value in container_binds:
            if (contains_compile(value) or _loads_any(value, ex.compiled_vars)) and base not in ex.compiled_vars:
                ex.compiled_vars.add(base)
                changed = True
        # a function that RETURNS a compiled subgraph is a factory: taint its name so a call `make_sub()` used as
        # an add_node action Load-references it → flagged → the Call inner can't be attributed → fail closed
        # (codex/Cursor r56). Over-tainting a factory only makes its uses fail closed (safe).
        for fname, rvals in func_returns:
            if any(contains_compile(rv) or _loads_any(rv, ex.compiled_vars) for rv in rvals):
                if fname not in ex.compiled_vars:
                    ex.compiled_vars.add(fname)        # bare-name call `make()` (Load-ref ∈ compiled_vars)
                    changed = True
                # ALSO a factory NAME, so an attribute/method call `Factory.make()` / `obj.make()` is caught in
                # the add_node arg scan by matching the .attr (codex r58 — classmethod/instance-method factory).
                ex.compiled_factory_names.add(fname)
        # a compiled subgraph stashed into a container via a METHOD (`lst.append(c)`, `s.add(c)`) taints the
        # receiver — same fail-closed effect as a subscript-assign stash (codex/Cursor r57).
        for base, args in method_stash:
            if base not in ex.compiled_vars and any(contains_compile(a) or _loads_any(a, ex.compiled_vars)
                                                    for a in args):
                ex.compiled_vars.add(base)
                changed = True
        # a function CALLED with a compiled subgraph as any argument → taint ALL its parameters, so a node
        # action that uses a parameter (`def use(sub): outer.add_node("s", sub)`) fails closed (codex/Cursor r60).
        for callee, args in name_calls:
            if callee in func_params and any(contains_compile(a) or _loads_any(a, ex.compiled_vars) for a in args):
                for p in func_params[callee]:
                    if p not in ex.compiled_vars:
                        ex.compiled_vars.add(p)
                        changed = True
        # a class attribute holding a compiled subgraph → its NAME, accessed as `C.attr`, is matched by the
        # add_node attribute check (r61).
        for attr, value in class_attr_binds:
            if attr not in ex.compiled_factory_names and (contains_compile(value)
                                                          or _loads_any(value, ex.compiled_vars)):
                ex.compiled_factory_names.add(attr)
                changed = True
        # a name whose decorator returns a compiled subgraph becomes a subgraph → taint the name (r61).
        for dname, decos in decorated:
            if dname not in ex.compiled_vars and any(
                    (isinstance(de, ast.Name) and de.id in ex.compiled_vars)
                    or (isinstance(de, ast.Name) and de.id in ex.compiled_factory_names)
                    or (isinstance(de, ast.Call) and isinstance(de.func, ast.Name)
                        and (de.func.id in ex.compiled_vars or de.func.id in ex.compiled_factory_names))
                    for de in decos):
                ex.compiled_vars.add(dname)
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
    # ESCAPE GUARD (codex r63): the add_node call itself can be obscured so node-counting silently undercounts —
    # `g.add_node` captured into a container/arg/attribute (`m={"a":g.add_node}`, `reg(g.add_node)`,
    # `setattr(h,"a",g.add_node)`, `fns=[g.add_node]`) or reached via `getattr(g,"add_node")`. The direct call
    # `g.add_node(...)` and the bound-method ALIASES that r63 tracks (`add=g.add_node`, partial, alias chains —
    # all assignments to a bare Name) are recognized and counted; ANY OTHER capture of `.add_node` as a value, or
    # a reflective `getattr(_, "add_node")`, means add_node calls may be hidden → fail closed for the unit.
    _safe_addnode = set()
    for nd in ast.walk(tree):
        tv = None
        if isinstance(nd, ast.Assign) and nd.targets and all(isinstance(t, ast.Name) for t in nd.targets):
            tv = nd.value
        elif isinstance(nd, (ast.AnnAssign, ast.NamedExpr)) and isinstance(nd.target, ast.Name) and nd.value:
            tv = nd.value
        if tv is None:
            continue
        if isinstance(tv, ast.Call) and call_name(tv).split(".")[-1] == "partial" and tv.args:
            tv = tv.args[0]
        if isinstance(tv, ast.Attribute) and tv.attr == "add_node":
            _safe_addnode.add(id(tv))
    _addnode_call_funcs = {id(c.func) for c in ast.walk(tree)
                           if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                           and c.func.attr == "add_node"}
    _escaped = any(isinstance(x, ast.Attribute) and x.attr == "add_node"
                   and id(x) not in _addnode_call_funcs and id(x) not in _safe_addnode
                   for x in ast.walk(tree))
    _getattr_addnode = any(isinstance(x, ast.Call) and isinstance(x.func, ast.Name) and x.func.id == "getattr"
                           and len(x.args) >= 2 and isinstance(x.args[1], ast.Constant)
                           and x.args[1].value == "add_node" for x in ast.walk(tree))
    if _escaped or _getattr_addnode:
        ex.features.append({"feature": "addnode-escaped", "line": 0})
    # CONSTRUCT-ESCAPE GUARD (Cursor r66): a Send/Command/interrupt is normally detected as the CALLEE of a call
    # (`Send(...)`) or via a tracked alias. If it is passed as an ARGUMENT to a function — `idfn(Send)(...)`,
    # `functools.partial(Send, ...)`, `lst.append(Send)`, `setattr(o,'x',Send)` — it can be invoked indirectly,
    # hiding a fan-out, and composition would emit a number → understatement. Any construct alias used as a call
    # argument (not the callee) fails closed. (Direct `Send(...)`, `S = Send`, tuple-unpack aliases, and
    # callees inside lists `[S(...)]` are NOT arguments, so legitimate detection is unaffected.)
    _construct_aliases = ex.send_aliases | ex.command_aliases | ex.interrupt_aliases
    _construct_escaped = False
    for c in ast.walk(tree):
        if isinstance(c, ast.Call):
            for a in list(c.args) + [k.value for k in c.keywords]:
                base = a.value if isinstance(a, ast.Starred) else a
                if isinstance(base, ast.Name) and isinstance(base.ctx, ast.Load) and base.id in _construct_aliases:
                    _construct_escaped = True
                    break
        if _construct_escaped:
            break
    if _construct_escaped:
        ex.features.append({"feature": "construct-escaped", "line": 0})
    # CONSTRUCT-RETURN-ESCAPE (codex r95): a construct RETURNED from a lambda/function — `(lambda: Send)()(...)`,
    # `def f(): return Send` — is a factory whose result can be invoked to fan out, bypassing the by-name callee
    # detection (the outer call's func is a Call, not a construct Name). Any construct alias that appears as a
    # NON-callee Load inside a lambda body or a `return` value means the construct escapes → fail closed. A
    # routing function that CALLS the construct (`lambda x: Send(x)`, `return Send("w", s)`) has it as a callee,
    # so it is detected as a normal fan-out, not an escape.
    if not _construct_escaped:
        for nd in ast.walk(tree):
            ret = nd.body if isinstance(nd, ast.Lambda) else (
                nd.value if isinstance(nd, ast.Return) and nd.value is not None else None)
            if ret is None:
                continue
            callees = {id(c.func) for c in ast.walk(ret) if isinstance(c, ast.Call)}
            if any(isinstance(x, ast.Name) and isinstance(x.ctx, ast.Load) and x.id in _construct_aliases
                   and id(x) not in callees for x in ast.walk(ret)):
                ex.features.append({"feature": "construct-escaped", "line": getattr(nd, "lineno", 0)})
                break
    # INTER-PROCEDURAL node-helper guard (Cursor r71): a function whose body adds nodes, CALLED ≥2 times or
    # inside a loop, materializes N runtime nodes from ONE textual add_node site → the flat node count
    # undercounts → fail closed. A node-adding helper called exactly once (not in a loop) is counted correctly.
    def _adds_nodes(fn):
        stk = list(fn.body)
        while stk:
            x = stk.pop()
            if isinstance(x, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue   # a nested function's add_node is ITS own
            if isinstance(x, ast.Call):
                if isinstance(x.func, ast.Attribute) and x.func.attr in ("add_node", "add_sequence"):
                    return True
                if isinstance(x.func, ast.Name) and x.func.id in ex.addnode_aliases:
                    return True
            stk.extend(ast.iter_child_nodes(x))
        return False
    _node_fns = {fn.name for fn in ast.walk(tree)
                 if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)) and _adds_nodes(fn)}
    if _node_fns:
        _par = {}
        for p in ast.walk(tree):
            for c in ast.iter_child_nodes(p):
                _par[id(c)] = p

        def _call_in_loop(node):
            cur = _par.get(id(node))
            while cur is not None:
                if isinstance(cur, (ast.For, ast.AsyncFor, ast.While, ast.ListComp, ast.SetComp,
                                    ast.DictComp, ast.GeneratorExp)):
                    return True
                if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                    return False
                cur = _par.get(id(cur))
            return False
        _counts, _loopcall = {}, False
        for c in ast.walk(tree):
            if isinstance(c, ast.Call) and isinstance(c.func, ast.Name) and c.func.id in _node_fns:
                _counts[c.func.id] = _counts.get(c.func.id, 0) + 1
                if _call_in_loop(c):
                    _loopcall = True
        if _loopcall or any(v >= 2 for v in _counts.values()):
            ex.features.append({"feature": "node-helper-multicall", "line": 0})
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
        "crewai_agent_budgets": ex.crewai_agent_budgets, "crewai_crews": ex.crewai_crews,
    }
    # feature 005: only when a subgraph-node is present, run the per-graph analysis for composition
    # (lazy import avoids a circular dependency; the flat path above is untouched for normal files).
    if any(f["feature"] == "subgraph-node" for f in ex.features):
        from costwright.subgraph import analyze
        out["subgraph_analysis"] = analyze(tree)
    return out
