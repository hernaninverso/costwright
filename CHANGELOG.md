# Changelog

All notable changes to costwright. Format loosely follows [Keep a Changelog](https://keepachangelog.com).

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

### Soundness (the cardinal rule: never understate)

Composition emits a number **only** for an inner graph that a static reader can fully pin down: a
straight-line sequence of direct `add_node` calls on a uniquely-bound, non-imported local
`StateGraph`. It **fails closed** (`non_certifiable`, no number) on every way the runtime graph or its
node count could differ from what the AST shows — hardened over a 9-round adversarial audit that found
and closed 10 distinct understatement paths:

- `RetryPolicy` / `error_handler` (per-node **and** graph-wide `set_node_defaults`), `**kwargs` spread —
  re-executions not modeled in v1.
- the subgraph **wrapper node** itself is counted (`n_total`, not `n_normal`).
- a subgraph with no own `recursion_limit` **inherits** the parent's (`max(parent, default)`), never less.
- a name **imported**, `from x import *`, or bound **more than once** (any Python binding form:
  assign/augassign/annassign/for/with/walrus/unpack/param/def/class/except-as/match-capture) →
  ambiguous → fail closed.
- a graph **(re)built or mutated inside a loop/comprehension**, **passed into a helper**, or built with
  **`add_sequence`** → node count not statically bounded → fail closed.
- cross-scope / class-body / parameter shadowing of a graph name → fail closed.

Dynamic rebinding (`globals()`, `setattr`, `exec`, `importlib`, monkeypatching) is unobservable to any
static analyzer and voids the certificate, as with every type checker — documented, out of scope.

The flat (non-subgraph) analysis path — 99% of files — is untouched.

## [0.1.0]

Initial release: static budget-ceiling certificates for LangGraph / CrewAI / OpenAI Agents SDK,
backed by a machine-checked (Lean 4) cost-soundness theorem. `check`, `caps`, `fuse` commands;
frozen `costwright.v1` JSON schema; zero runtime dependencies.
