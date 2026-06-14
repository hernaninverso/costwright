# Changelog

All notable changes to costwright. Format loosely follows [Keep a Changelog](https://keepachangelog.com).

## [0.2.1] — 2026-06-14

### Soundness hardening — ~35 understatement paths closed (adversarial audit, codex + Cursor `gpt-5.3-codex`)

A second, exhaustive adversarial soundness pass (the cardinal rule: a bound must **never** understate the true
per-run worst-case node-activation count). Every finding below was a real understatement reproduced by running
the analyzer, then fixed and pinned with a regression test. Two reported findings were adjudicated **false
positives** and documented: an edge to a node never `add_node`'d is an *invalid* graph (LangGraph `compile()`
raises), and the per-run ceiling for a `batch([N])` host is correct (it is N independent per-run runs; the
aggregate is out of the per-run metric).

**Subgraph composition (feature 005) — both escape dimensions closed.** A compiled subgraph reaching `add_node`
through *any* same-file indirection now composes or fails closed (never silently flattens to one node): module-
attribute / `getattr` / `vars` / `__dict__` / `globals` reflection (bound **and** direct-called), reflective
namespace access, 1-arg `add_node(inner.compile())`, container/attribute stash (by assignment, by method
`append`/`add`/`update`, by `setattr`), subgraph **factory** functions (bare / classmethod / instance / `self`
/ nested / list-&-dict-comprehension / module-level / ternary / generator-`yield`), augmented assignment,
**function-parameter pass-through**, class attribute, decorator-returns-subgraph; a file mixing one attributable
and one un-attributable subgraph now fails closed (completeness guard). The `add_node` **call** can also be
obscured — captured into a container/argument/attribute or via `getattr` — which now fails closed
(`addnode-escaped`), while the recognized bare-Name/`partial`/alias-chain forms are counted. A
`Send`/`Command`/`interrupt` passed as a call **argument** (higher-order `idfn(Send)(...)`, `partial`,
`append`) fails closed (`construct-escaped`).

**Flat (non-subgraph) path — pre-existing understatements fixed.**
- `add_sequence([...])` now counts one node per element (was zero); a non-literal sequence fails closed.
- the `linear` bound (= supersteps) is used only for a true chain; **static fan-out** (a source with ≥2
  successors, e.g. `START`→many) now bounds at `supersteps × n_nodes`.
- a node `RetryPolicy` / `error_handler` / `**kwargs` / graph-wide `set_node_defaults` re-executes a node →
  fail closed (`node-unmodeled-retry`).
- `add_node` inside a loop/comprehension, or in a **helper function called ≥2 times / in a loop**, materializes
  N runtime nodes from one site → fail closed (`node-in-loop` / `node-helper-multicall`).
- an explicit bound `< 1` (recursion_limit/max_iter/max_turns ≤ 0) no longer yields a zero/negative ceiling →
  fail closed.
- **multiple** explicit bounds of one param now combine instead of taking the first: LangGraph invokes are
  separate runs → `max(recursion_limit)`; CrewAI agents / Agents-SDK handoffs are sequential → `sum`.

Cross-module imported factories and fully-dynamic reflection (`eval`/`exec`/non-literal `getattr`/monkeypatching)
remain a documented limitation of any static analyzer — they void the certificate rather than producing a number.

## [0.2.0] — 2026-06-14

### Added — nested subgraph bound composition (feature 005)

`graph.add_node("x", inner.compile())` (a compiled subgraph used as a node) used to map to
`non_certifiable`. costwright now **composes** the bound when it is sound to:

```
bound(outer) = outer_steps × ( n_total_nodes(outer) + Σ_subgraph-nodes bound(inner) )
```

under the **no-fan-out invariant** (a node activates ≤ once per super-step absent `Send` /
dynamic-`goto`, which stay blocking). The composed ceiling is an upper bound and is **conservative by
construction** — it never understates.

A nested subgraph inherits the **parent run's** `recursion_limit` (its standalone
`inner.compile().invoke(recursion_limit=N)` is a *separate* run that does not constrain it as a node),
so a composed subgraph runs at `max(parent, default)` and the composed bound is conservatively
`default_dependent` — honest, not the falsely-tight number an "inner uses its own limit" model gives.

### Soundness (the cardinal rule: never understate)

Composition emits a number **only** for an inner graph that a static reader can fully pin down: a
straight-line sequence of direct `add_node` calls on a uniquely-bound, non-imported local
`StateGraph` used only as a method-call receiver. It **fails closed** (`non_certifiable`, no number) on
every way the runtime graph, its node count, or its limit could differ from what the AST shows —
hardened over a **33-round adversarial audit** (codex CLI + Cursor `gpt-5.3-codex`, final clean APPROVE)
that found and closed **34 distinct understatement / unsound-output paths**, including:

- `RetryPolicy` / `error_handler` (per-node **and** graph-wide `set_node_defaults`), `**kwargs` spread —
  re-executions not modeled in v1.
- the subgraph **wrapper node** itself is counted (`n_total`, not `n_normal`).
- a subgraph with no own `recursion_limit` **inherits** the parent's (`max(parent, default)`), never less.
- a name **imported**, `from x import *`, or bound **more than once** (any Python binding form:
  assign/augassign/annassign/for/with/walrus/unpack/param/def/class/except-as/match-capture) →
  ambiguous → fail closed.
- a graph **(re)built or mutated inside a loop/comprehension**, **passed into a helper**, mutated via a
  **bound-method alias** (`f = g.add_node`), a **container** (`mutate([g])`), or built with
  **`add_sequence`** → node count not statically bounded → fail closed.
- cross-scope / class-body / parameter shadowing of a graph name → fail closed.
- the `recursion_limit` is read from every invocation method (`invoke`/`stream`/`batch`/…), positional or
  keyword, as the **max over all call sites** (a no-config invoke contributes the default); any
  unreadable form (named/non-literal config, `*args`/`**kwargs`, computed/non-constant key, negative
  limit, `with_config`) → fail closed.
- a compiled subgraph reaching `add_node` through **any** binding/alias shape (assign / walrus /
  annotated / tuple-unpack / subscript / wrapped call / attribute / alias chain) is detected → composed
  or fail-closed, never silently flattened to one node.

Dynamic rebinding (`globals()`, `setattr`, `exec`, `importlib`, monkeypatching) is unobservable to any
static analyzer and voids the certificate, as with every type checker — documented, out of scope.

The flat (non-subgraph) analysis path — 99% of files — is untouched.

### Metric: the ceiling is PER RUN

`node_executions_ceiling` bounds **one** graph execution (`recursion_limit` is a per-run LangGraph concept).
`batch`/`abatch` run the graph **once per input element** — N independent runs, each separately bounded by the
same per-run ceiling, exactly like calling `invoke` in a loop (which is also reported per-run, never multiplied
by the loop count). The aggregate cost of a batch call is `(per-run ceiling) × (batch cardinality)`; that
cardinality is outside the per-run metric and is the caller's to apply. costwright deliberately does not fold a
literal batch length into the number (it would make the metric depend on calling syntax) nor fail closed on a
soundly per-run-bounded batch.

## [0.1.0]

Initial release: static budget-ceiling certificates for LangGraph / CrewAI / OpenAI Agents SDK,
backed by a machine-checked (Lean 4) cost-soundness theorem. `check`, `caps`, `fuse` commands;
frozen `costwright.v1` JSON schema; zero runtime dependencies.
