# Feature Specification: subgraph bound composition (v0.2.0)

**Feature Branch**: `005-subgraph-bound-composition`
**Created**: 2026-06-13
**Status**: Draft (awaiting council)
**Origin**: real user on r/LangChain (u/Ha_Deal_5079): *"¿maneja subgrafos recursivos o nomás
gráficas planas de nodos?"* — confirmed costwright caught their token-chewing branching, asked about
nested subgraphs. Today a subgraph node returns `non_certifiable` (the honest-but-not-useful answer).

## Problem

A LangGraph subgraph node — `g.add_node("sub", subgraph.compile())`, where the node's handler is ANOTHER
compiled graph — is detected (`extract.py` tags `subgraph-node`) and conservatively BLOCKED
(`mapper.BLOCKING` → `no-mapeable:subgraph-node` → public `non_certifiable`). costwright says "I can't
bound this" even when both the outer and inner graphs have perfectly resolvable ceilings.

## Goal

Compose the bound so a subgraph node becomes **certifiable** (or `default_dependent`, conservatively)
when the inner subgraph's ceiling is statically resolvable — instead of a blanket `non_certifiable`.

## Soundness (the crux — REVISED after council 2026-06-13, which refuted the naïve claim)

`recursion_limit` is the max super-steps **per graph invocation** (verified vs LangGraph docs, v1.0.6:
default 1000). A subgraph used as a node runs as a **sub-invocation** each time the outer node executes.

**The naïve claim `outer_ceiling × inner_ceiling` is UNSOUND in general** (council, unanimous): a single
outer super-step can launch **N parallel activations** of the same subgraph-node via `Send` (fan-out), so
`outer_supersteps` does NOT bound the node's *activations* — it bounds super-steps, and a super-step can
run a node many times in parallel. With N parallel Sends, the subgraph runs `N × inner_ceiling` in one
super-step, and N can exceed `outer_ceiling`. The product would **understate** → a lying certificate.

**Correct bound (codex):** `total_internal_supersteps ≤ activation_bound(node) × inner_ceiling`, where
`activation_bound` = a static upper bound on how many times the subgraph-node is ever ACTIVATED across the
whole outer run (including fan-out and retries).

**What makes the feature sound IN SCOPE — the no-fan-out invariant.** `send-fanout` is ALREADY a
`BLOCKING` feature (any `Send` in the graph ⇒ `no-mapeable:send-fanout` ⇒ `non_certifiable`, evaluated
BEFORE composition). So in the path where composition runs, **there is no `Send`**. Under LangGraph's
BSP/Pregel model, absent `Send` a node is activated **≤ once per super-step** (parallel branches run
DIFFERENT nodes once each; conditional edges / loops schedule work for LATER super-steps — they do NOT
re-enter within a super-step, per codex). Therefore `activation_bound = outer_ceiling`, and only then:

> `composed_bound = outer_ceiling × inner_ceiling`   — sound **because** the no-fan-out invariant holds.

The composition path MUST re-assert this invariant (FR-007): if a `Send` or any other fan-out / a
finite-but-multiplying construct reaches the subgraph node, do NOT compose — stay `non_certifiable`.
**Retries** (a `RetryPolicy` re-runs a node within the same super-step) are an `activation_bound`
multiplier: a finite `max_attempts` multiplies it; an unbounded/unknown retry ⇒ `non_certifiable`. (This
retry gap pre-exists in the base bound too; documented as a known conservative limitation, not worsened.)

This generalizes the existing per-graph bound `supersteps × Σ(per-node cost)` where a normal node costs
1: under the no-fan-out invariant a **subgraph node costs its inner `bound_factor`** instead of 1.
**Never an understatement** — same discipline as the CP conservatism in 004.

## Provenance composition (ABSORBING — council P0: not "max", absorbing)

`non_certifiable` and `runaway` are **absorbing**: if ANY component (outer or any nested inner) is
non_certifiable or runaway, the composite inherits that state and **carries NO numeric ceiling** (so an
unbounded inner can never hide behind a small outer ceiling — council). Lattice, worst absorbs:
- any component `non_certifiable` ⇒ composite **non_certifiable** (no number).
- else any component `runaway` ⇒ composite **runaway** (no number).
- else any component `default_dependent` (relies on a framework default) ⇒ composite **default_dependent**.
- else all `certifiable` ⇒ composite **certifiable** with the product `bound_factor`.
- composed product ≥ `HUGE_LIMIT` ⇒ `rechaza-con-razon` (runaway): the cap is effectively vacuous.

## Scope (v1 — minimal, sound, shippable)

**IN:**
- Single level of nesting: outer graph with one or more subgraph nodes whose inner `StateGraph` is
  **defined in the SAME file** (statically resolvable: find the `X` in `X.compile()`, locate its
  `StateGraph(...)` construction + its `recursion_limit`/edges, run the SAME extraction recursively).
- Multiple subgraph nodes in one outer graph (sum their inner bound_factors).
- Bounded-depth recursive composition (a subgraph that itself contains a subgraph), with a **hard depth
  cap** (e.g. 5) and **cycle detection** on the subgraph-definition graph → a self/mutually-referential
  subgraph definition ⇒ `runaway` (not infinite recursion in the analyzer).

**OUT (v1 → honest `non_certifiable`/`default_dependent`, documented):**
- Subgraph imported from another module / not statically locatable in the analyzed file set.
- Subgraph variable assigned dynamically / behind a function call.
- These return the current conservative answer; the README/`--help` states the limitation.

## Functional requirements

- **FR-001** `extract.py`: when a `subgraph-node` is found, also capture the **inner graph reference**
  (the variable/name in `X.compile()`) and, if `X = StateGraph(...)` is constructed in the same module,
  recursively extract the inner graph's `ExtractionResult` (nodes, edges, bounds, cycles, nested
  subgraph features). Attach it to the feature: `{"feature":"subgraph-node","line":N,"inner":<ref>}`.
- **FR-002** `mapper.py`: REMOVE `subgraph-node` from `BLOCKING`. When present and the inner graph
  resolved, recursively `map_unit` the inner graph → get its `bound_factor` + category, then COMPOSE
  (per Soundness/Provenance above). When the inner is unresolved (scope OUT) ⇒ keep `non_certifiable`.
- **FR-003** Recursive composition is depth-capped (≤ 5) and cycle-guarded on the subgraph-definition
  graph; exceeding the cap or a definition cycle ⇒ **`non_certifiable`** (can't analyze — codex: NOT
  `runaway`, which would falsely imply "unbounded"), never analyzer recursion. Fail-closed, show the path.
- **FR-007 (council P0 — the soundness guard)** Composition is attempted ONLY under the no-fan-out
  invariant. If the analyzed graph has a `Send` (already `BLOCKING` ⇒ non_certifiable, checked first) or
  any construct that could activate the subgraph node more than once per super-step, do NOT compose. A
  node with a `RetryPolicy`: finite `max_attempts` ⇒ multiply `activation_bound`; unbounded/unknown retry
  ⇒ `non_certifiable`. The composed certificate records `activation_bound` + the no-fan-out assertion.
- **FR-004** The composed `bound_factor` carries provenance showing the composition
  (`"bound_source": "outer(explicit 50) × subgraph 'sub'(explicit 25) = 1250"`), so the certificate is
  auditable. `HUGE_LIMIT` applies to the COMPOSED product.
- **FR-005** Backward-compatible: graphs with NO subgraph nodes are byte-identical to today (the new path
  only triggers on a `subgraph-node` feature). The `costwright.v1` schema is unchanged (the composed unit
  is a normal `certifiable`/`default_dependent`/etc. unit with a richer `bound.provenance` string).
- **FR-006** New fixtures: `examples/workflows/` gains a certifiable nested-subgraph graph + a
  default-dependent one (inner without limit) + a runaway one (recursive subgraph def). Demo/README show
  the composed ceiling.

## Verification gates
- All existing tests pass (no regression; non-subgraph paths unchanged).
- New tests: composition arithmetic (outer×inner), worst-provenance, depth-cap/cycle → runaway,
  same-file resolution, cross-file → conservative non_certifiable, HUGE on the product → reject.
- **Soundness check:** a property test confirming the composed bound is ALWAYS ≥ the true worst-case
  (enumerate small nested graphs, simulate worst-case execution count, assert composed ≥ simulated).
- Council (soundness of the product bound + provenance) + audit-3 before merge. Then release v0.2.0.

## Council gate — RESUELTO (council-v2, 6 voces / 4 modelos, 2026-06-13): GO-con-cambios

The council **refuted** the naïve `outer × inner` claim (unanimous) — UNSOUND under `Send` fan-out. P0s,
all incorporated above:
- **P0-1 (formula):** `composed = activation_bound × inner_ceiling`. `activation_bound = outer_ceiling`
  ONLY under the no-fan-out invariant (FR-007); the existing `send-fanout` BLOCKING provides it. Any
  fan-out ⇒ non_certifiable.
- **P0-2 (absorbing provenance):** non_certifiable/runaway absorb + drop the numeric ceiling (Provenance §).
- **P0-3 (codex, depth/cycle):** exceeding depth-cap or a definition cycle ⇒ `non_certifiable`, NOT
  `runaway` (FR-003).
- **P0-4 (codex, reentrancy):** conditional edges / normal loops do NOT re-enter within a super-step (they
  schedule later) — NO separate reentrancy factor (rejected the majority's `max_reentrancy`). Only `Send`
  and `RetryPolicy` multiply activations (FR-007).
- **P0-5 (imports):** imported / unresolved subgraph ⇒ explicit `non_certifiable` with the path/reason,
  never a silent pass (FR-002 scope-OUT).
- **P0-6 (retries):** finite RetryPolicy ⇒ activation multiplier; unbounded ⇒ non_certifiable. Documented
  as a pre-existing conservative gap of the base bound (not worsened here).

Codex (lead) GO-con-cambios, confidence 98, citing LangGraph super-steps/Send + subgraphs docs. Remaining:
implement per the above + a **soundness property test** (simulate worst-case nested execution, assert
composed ≥ simulated) + audit-3 before release v0.2.0.
