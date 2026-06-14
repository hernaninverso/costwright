# Changelog

All notable changes to costwright. Format loosely follows [Keep a Changelog](https://keepachangelog.com).

## [0.2.6] — 2026-06-14

### CrewAI cost-model rebuild + Runner-subclass discovery (codex + Cursor `gpt-5.3-codex`)

- **Runner subclass discovery** (codex + Cursor r85) — `class UnboundedRunner(Runner): pass` then
  `UnboundedRunner.run_sync(..., max_turns=None)` was dropped by `_find_units` (the receiver was the subclass
  name, not `Runner`), so a runaway passed `--fail-on reject` with exit 0. Subclass names are now collected
  (fixpoint over transitive subclasses, base resolved via the import alias) and their `run*` calls counted.
- **CrewAI per-run bound rebuilt to `n_tasks × max(agent budget)`** (codex r86 + r86b, Cursor r86) — the old
  model summed only the *explicit* agent `max_iter` budgets, which **ignored the task count** (a single agent
  reused across `k` tasks understated `k×`) and **omitted the framework default 20** of unannotated agents in a
  mixed crew. A sequential crew runs each of `n_tasks` tasks with up to its agent's `max_iter`, so the per-run
  worst case is `n_tasks × max(agent budget)` (default 20 included). The extractor records every agent budget
  and each crew's literal task/agent counts; the mapper **fails closed** when the task list is absent/dynamic,
  no agents are visible, or a crew references more agents than visible constructors.
- **`allow_delegation=True` fails closed** (Cursor r86) — a delegating agent invokes another agent's `max_iter`
  loop inside a task (a recursive delegation tree the model does not bound) → `no-mapeable:agent-delegation`
  unless `allow_delegation` is a constant `False`.
- **Task guardrail-retry fails closed** (codex r87) — a `Task(guardrail=...)` re-runs on a failed check (up to
  `max_retries`), so its agent loop runs `(1 + retries)×`; any guarded task → `no-mapeable:crewai-task-retry`.
- **Unknown `Task(agent=…)` fails closed** (Cursor r87) — an agent referenced only via `Task(agent=heavy)` where
  `heavy` is imported / not a visible `Agent()` constructor could carry a far larger `max_iter` than the
  visible agents, so `max(visible)` understated. A prepass binds Names to `Agent()` constructors; a task
  referencing an unknown agent → `no-mapeable:crewai-agent-unknown`.

## [0.2.5] — 2026-06-14

### Continued whole-tool audit — four more understatement/false-assurance paths (codex + Cursor `gpt-5.3-codex`)

- **Dynamic Chat-API model name** (codex r83) — a model name that is not a string constant (`"gpt-" + "5"`
  concat, a `Name` bound elsewhere, an f-string, `os.environ[...]`) could resolve to a reasoning model at
  runtime, where Chat-API `max_tokens` is IGNORED. `caps` assumed non-reasoning and accepted `max_tokens=1` as
  effective → "all capped" false assurance. A dynamic model on `ChatOpenAI`/`AzureChatOpenAI` now requires
  `max_completion_tokens` (the cap that holds for reasoning AND non-reasoning Chat models).
- **Reflective LLM constructor** (Cursor r83) — `importlib.import_module("langchain_openai")` +
  `getattr(m, "ChatOpenAI")` then `Ctor(...)` reached a known constructor by string, escaping the by-name
  `caps` scan → an uncapped LLM reported as "all capped". A literal `getattr(_, "<KnownCtor>")` now fails closed,
  and the all-clear message no longer claims completeness (it states reflective/dynamic construction is not
  covered by a static by-name scan).
- **Aliased `Runner` receiver** (codex r84) — `from agents import Runner as R` then
  `R.run(..., max_turns=None)` was dropped by `_find_units` (the literal `"Runner.run"` precheck missed the
  aliased call and the AST resolved the dotted string `R.run`, never the alias map). A runaway
  (`max_turns=None` disables the bound) silently passed `--fail-on reject` with exit 0. The receiver alias is
  now resolved before composing `Runner.run`/`run_sync`/`run_streamed`.
- **`add_sequence` factory-method subgraph** (Cursor r84) — `add_sequence([('sub', Factory.make())])` where
  `Factory.make()` returns `inner.compile()` silently certified an understated flat bound: `add_sequence`
  checked only inline-`.compile()` and compiled-var references, NOT the factory-method attribute that
  `add_node` already detects. `add_sequence` now mirrors `add_node` exactly and routes the element to
  compose/fail-closed.

## [0.2.4] — 2026-06-14

### Continued whole-tool audit — three more understatement/false-assurance paths (codex + Cursor `gpt-5.3-codex`)

- **Standalone middle subgraph in a 3-level nest** (codex r82, BLOCKER) — `leaf→mid→outer` where the MIDDLE
  subgraph is ALSO invoked standalone at a far-larger limit than the outer. `compose()` reported only the
  unique outer composition and hid the bigger mid-standalone run (witness: outer `recursion_limit=2` →
  2,002,002 reported, but `mid.compile().invoke(recursion_limit=9000)` is a separate top-level run worth
  81,009,000). `compose()` now resolves EVERY top-level run — the unique outer PLUS any inner that is also
  invoked standalone — and reports the MAX bound. The pre-existing "standalone ignored" test was itself
  encoding the understatement and was corrected to the conservative value.
- **fusion RISK glyph not verdict-aware** (codex r82) — `pretty()` rendered non-abstained `Refuted` and
  `Conflicting` verdicts with the same `✓` glyph as `Supported` → reassuring summary on a contradicted claim.
  New `_risk_glyph()` is verdict-aware: `Supported=✓`, `Refuted=✗`, `Conflicting=⚠`, `Not Enough Evidence=▲`.
- **Import-aliased LLM constructors** (codex + Cursor r81) — `from langchain_openai import ChatOpenAI as LLM2`
  then `LLM2(...)` escaped the by-name `caps` lookup → an uncapped constructor reported as "all capped" (false
  assurance). `caps.scan_file` and `cli._find_units` now resolve `ImportFrom ... as` aliases before lookup.

## [0.2.3] — 2026-06-14

### Continued whole-tool audit — false-assurance paths in the secondary features (codex + Cursor `gpt-5.3-codex`)

- **CrewAI hierarchical via alias** — a hierarchical Crew runs a manager that re-delegates (an unbounded loop).
  The detection caught the literal `process=Process.hierarchical`/`"hierarchical"` but NOT an aliased
  `mode = Process.hierarchical; process=mode` (enum or string), which certified a finite bound. Now only a
  confirmed-sequential LITERAL is safe; a hierarchical literal/alias/variable/computed value fails closed.
- **`**kwargs` spread on invoke/run** — `Runner.run(a, **{"max_turns": None})` (None disables the cap) or
  `app.invoke({}, **opts)` let a disabling/overriding bound survive unseen, so the analyzer fell back to the
  framework default and certified. Any `**` spread on an invoke/run call now records an unresolved bound and
  fails closed.
- **fusion honesty-field injection** — the conditional-analysis allowlist copied caller values for the
  honesty/provenance strings, so a caller could inject `disclaimer="GUARANTEED SAFE"`, `note`,
  `open_channels=["none"]`, `channel_covered`/`source_estimator` into the signed bundle. These are now FORCED to
  costwright's own constants; only measured primitives come from the caller (and ε is recomputed).
- **caps `make_patch`** — inserted the cap kwarg right after `(`, producing `Ctor(kwarg=…, "positional")` =
  SyntaxError when the constructor had a positional arg; the suggested fix was invalid Python. Now the kwarg is
  inserted as the LAST argument (AST `end_col_offset`), robust to positional args / strings / nesting; a
  reasoning model passed positionally (`ChatOpenAI("gpt-5")`) is recognized so the correct kwarg is suggested.
- **caps `--cap < 1`** rejected (a 0/negative cap would emit an inert `max_tokens=0`).

## [0.2.2] — 2026-06-14

### Whole-tool soundness audit — fusion / report / caps / cli hardened (codex + Cursor `gpt-5.3-codex`)

The adversarial audit was extended past the analyzer to every module. Both auditors independently confirmed
`fusion.py` is conservative (the Clopper-Pearson upper `_cp_upper` is ≥ an independent high-precision
Clopper-Pearson on 700+ adversarial `(k, m, η)` including η<2⁻⁵³; the cost side always reports the WORST unit
category; `composition.joint_guarantee` is always false; malformed cost/risk input fails closed). Fixes:

- **fusion.py** — `_inflate_alpha` clamps ε≥0 and floors at the base α (a negative ε no longer *decreases* α,
  which would understate risk); `conditional_analysis_from_epsilon` now RECOMPUTES ε-upper and the channel-1
  bound from the primitives (k, m, δ_eps, α, c; m capped at 1e9) instead of shipping the caller's reported
  numbers, so its standalone output is authoritative — matching the `fuse()` recompute.
- **caps.py** — an *effective* token cap now requires the constructor's CORRECT kwarg (per provider, after the
  reasoning-model adjustment) present as a positive-int literal. A wrong kwarg for the constructor (`max_tokens`
  on OpenAI's Responses API, whose cap is `max_output_tokens`; `max_tokens` on `ChatOllama`, whose cap is
  `num_predict`) or a non-literal / non-positive value (`None`/`-1`/variable/`True`) is flagged `ineffective`,
  not treated as bounded. A file that does not parse but mentions an LLM constructor surfaces a `parse_error`
  finding instead of being silently counted as "all capped".
- **cli.py** — `--fail-on` tiers are now monotonic and complete: a `parse_error` (a unit costwright could not
  analyze) no longer silently passes, and a `non_certifiable` unit also trips the stricter `default-dependent`
  threshold. `reject ⊆ non-certifiable ⊆ default-dependent`.
- **report.py** — `pretty()` uses a defensive badge lookup so the human output never crashes on an unexpected
  category (the JSON `public_category` mapping was already fail-closed; `node_executions_ceiling == bound_factor`).

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
