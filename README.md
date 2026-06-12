# gasket

[![Paper DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.20661093.svg)](https://doi.org/10.5281/zenodo.20661093)

**Static budget certificates for LLM-agent workflows.** Point it at your LangGraph / CrewAI /
OpenAI-Agents-SDK repo and it tells you — *before anything runs* — the worst-case budget ceiling of
every workflow graph, where that ceiling comes from, and when it is effectively vacuous.

Backed by a machine-checked (Lean 4) cost-soundness theorem:
[*A Potential-Based Calculus for Resource Bounds of LLM-Agent Workflows*](https://doi.org/10.5281/zenodo.20661093) ([repo](https://github.com/hernaninverso/typed-resources))
— well-typed ⟹ spend ≤ declared budget, on every trace. gasket is the static frontend of that
calculus. It **never executes your code** (pure AST analysis, zero dependencies).

```
pip install gasket-check
gasket check .
```

```
gasket — budget certificate check  (schema gasket.v1)

  ▲ src/agents/researcher.py :42   default_dependent  ≤1000 supersteps (framework_default)
  ‼ scripts/run_forever.py   :17   runaway            while-true-driver
  ✗ src/agents/fanout.py     :88   non_certifiable    send-fanout

  12 graph units | ✓ 3 certifiable | ▲ 7 default-dependent | ✗ 1 non-certifiable | ‼ 1 runaway

  ⚠ 6 unit(s) rely on a framework default of ≥1000 supersteps (LangGraph ≥1.0.6) —
    that budget ceiling is effectively vacuous. Set recursion_limit explicitly.
```

## Why

We measured **254 real agent workflows** from 45 public production-grade repos:

- **88%** of cyclic workflows rely on a *framework default* for their only budget bound — or have
  none at all.
- LangGraph's modern default (`recursion_limit`) is **1000 supersteps** (it was 25 before v1.0.6).
  A "protected" workflow can take a thousand turns before the framework stops it.
- Only **12%** of LLM calls in real code carry an explicit token cap.

A runtime budget tracker tells you what you spent — after you spent it. A *static* certificate
tells you the worst case before you deploy, rejects the runaway pattern at check time, and the
math behind the bound is machine-checked, not vibes.

<sub>Methodology disclosure: dataset is ~80% LangGraph (204/254 units; CrewAI and Agents-SDK
samples are small); units cluster ~5.6 per repo; public-GitHub visibility bias applies; LangGraph
version assumed ≥1.0.6 (default 1000) where undeclared; false-reject 0 was measured on N=3
surviving rejects. Full experiment: spec, frozen dataset, and audit trail in this repo's
`specs/` and `results/`.</sub>

## Commands

### `gasket check [path]`

Maps every workflow graph to the typed-budget calculus and reports, per graph unit:

| Category | Meaning |
|---|---|
| `certifiable` | bound is explicit in your code — real certificate |
| `default_dependent` | bound exists only via a framework default (often vacuous) |
| `non_certifiable` | uses a construct outside the calculus (Send fan-out, interrupts, hierarchical manager, dynamic goto, subgraph-as-node) |
| `runaway` | genuinely unbounded (`while True` driver, `max_turns=None`, astronomically large limit) |
| `parse_error` | the analyzer could not reconstruct the graph (reported, never silent) |

- `--json` → stable [`gasket.v1`](#json-schema) output for CI/tooling.
- `--fail-on reject|default-dependent|non-certifiable` → exit 1 on policy violation
  (default: **never fails** — findings are warnings; strictness is opt-in).
- Exit codes: `0` ran fine, `1` policy violated, `2` infrastructure error. Never anything else.

### `gasket caps [path]`

Finds LLM constructors without a token cap and tells you the **right parameter for that provider**
(it is parameter-specific, not provider-specific — verified against primary provider docs):

- OpenAI Responses → `max_output_tokens` (bounds reasoning+output)
- Azure/OpenAI reasoning (Chat) → `max_completion_tokens` (reasoning tokens are inside it)
- Anthropic standard → `max_tokens` (with `budget_tokens < max_tokens`); **interleaved/adaptive
  thinking degrades the ceiling** (budget may exceed `max_tokens`) — flagged
- Gemini → `max_output_tokens` **plus** `thinking_budget` (thinking is billed as output but NOT
  bounded by `maxOutputTokens`) — flagged when missing

`--patch out.diff` emits a unified diff (`git apply out.diff`). **gasket never edits your files.**

### GitHub Action

```yaml
- uses: hernaninverso/gasket/action@v0.1
  with:
    fail-on: reject        # reject | default-dependent | non-certifiable | none
```

## JSON schema

`gasket.v1` is frozen: closed enums (`category`, `provenance`, `aggregation`), stable `unit_id`,
line spans only (no source code in output — CI-log safe). New fields are additive in `v2`.
The `signature` field is reserved (`null` in OSS output): signed, independently verifiable
certificates are a separate service — contact below.

## What gasket does NOT do (honest scope)

- It does not bound `Send` fan-out, interrupts/human-in-the-loop, CrewAI hierarchical mode,
  dynamic `goto`, or subgraphs-passed-by-variable — those map to `non_certifiable`, never to a
  fake bound. Conservative by construction: when in doubt, no certificate.
- The bound is worst-case (deliberately over-approximate). Tightening it is roadmap.
- A token-level dollar bound additionally needs caps on every call (`gasket caps`) and a
  per-provider billing ceiling — see §3.2 of the paper for where that holds and degrades.

## Theory

The calculus (7 constructs, affine potential in the style of Hofmann–Jost AARA), its cost-soundness
theorem, the relational↔functional equivalence, the termination measure, and the affine
no-double-spend layer are machine-checked in Lean 4 (no `sorry`; axioms `[propext, Quot.sound]`):
[hernaninverso/typed-resources](https://github.com/hernaninverso/typed-resources).

## Certification / enterprise

Signed budget certificates (Ed25519, independently verifiable), a live per-provider billing-ceiling
feed (providers change semantics monthly — your certificate can silently invalidate), and org-level
CI policy are a paid layer on top of this OSS tool. Contact: hernaninverso@gmail.com.

## License

Apache-2.0.
