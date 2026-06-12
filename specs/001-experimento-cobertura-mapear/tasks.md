# Tasks: Experimento de cobertura — grafos reales → cálculo de potencial

**Input**: plan.md + spec.md v2 (consenso council 6/6)
**Unidad de done**: cada task tiene gate verificable; los números finales salen de results.json.

## Phase F1 — Freeze (dataset congelado)

- [ ] T001 `harness/freeze.py`: barrer `dataset/repos/**/*.py` (excl. venv/site-packages/node_modules);
      detectar **graph units** (D1): `StateGraph(...)` asignado+compilado (LangGraph), `Crew(...)`
      (CrewAI), `Runner.run*(` entrypoints (Agents SDK). Unidad = objeto, no archivo (un archivo
      puede aportar 0..N units).
- [ ] T002 Dedup estructural (D6c): hash de AST normalizado (identificadores→`_`, sin literales,
      sin docstrings) del scope que define la unit; descartar duplicados registrando motivo.
- [ ] T003 Congelar: copiar archivos involucrados a `dataset/frozen/<unit_id>/` + manifest
      (repo/path/SHA/stars/bucket/framework/hash). **Gate F1: ≥50 units únicos** (si no: ampliar
      recolección con más repos, sin tocar filtros).

## Phase F2 — Extract + Map

- [ ] T004 `harness/extract.py`: por unit, AST walk → nodos (`add_node`), edges (`add_edge` /
      `add_conditional_edges` con dict-literal vs función / `Command(goto=...)` literal vs computado
      / `Send`), ciclos (DFS sobre el grafo reconstruido), entry/exit. Resolución intra-repo de
      1 salto para nodos importados (D-deepseek); si falla → `extractor-failure:cross-file`.
- [ ] T005 Bounds (D2/D8): buscar `recursion_limit` en invoke/stream/batch configs y RunnableConfig;
      `max_iter` en Agents CrewAI; `max_turns` en Runner.run; drivers externos `while True`/`for`
      que re-invocan; detectar versión de langgraph (requirements/lockfile) para el default 25/1000.
      Cada bound con fuente: `explicit | framework-default | absent` + span.
- [ ] T006 Caps: `max_tokens`/`max_output_tokens`/`max_completion_tokens`/`budget_tokens` en la
      construcción de modelos/llamadas de cada nodo → certifiability por call.
- [ ] T007 `harness/mapper.py`: árbol D4 estricto (orden 1→5, primera que aplica). Presupuesto
      global D2: `n·Σ(nodos)` default; `n·max` solo con cadena lineal probada; `Send` →
      `no-mapeable:send-fanout`. Término del cálculo o motivo SIEMPRE registrado con evidencia.
- [ ] T008 `tests/test_harness.py`: 1 fixture sintética POR categoría D4 (5) + fixture multi-unit +
      fixture cross-file + fixture Send + fixture while-True driver. **Gate F2: todos pasan.**

## Phase F3 — Measure

- [ ] T009 `harness/metrics.py`: distribución por categoría; % mapeable (3+4+5); certifiability
      (% calls con cap finito); value-prop runaway (SC-004); cota numérica resultante por unit
      (D8: con default 1000 el certificado es casi vacuo — surfacearlo); veredicto mecánico SC-001..005.
- [ ] T010 `harness/run_experiment.py` + `results/results.json` + `results/report.md`. **Gate F3:
      dos corridas → bytes idénticos** (seed fija, sin timestamps dentro del JSON).
- [ ] T011 `harness/review_pack.py`: TODOS los cat-1/2/3 + 10 de cat-4/5 (seed fija) → `results/
      review/<unit>.md` con código resaltado y término/motivo lado a lado (D5).

## Phase F4 — Revisión manual (D5)

- [ ] T012 Revisar el 100% del review pack leyendo el código real; clasificar falso-rechazo /
      falso-aceptado / correcto; dudosos marcados para Hernán. Completar los campos en report.md.

## Phase F5 — Audit de resultados (6 frontera)

- [ ] T013 Someter results.json+report.md a los 6 (¿números creíbles? ¿veredicto sigue de los
      datos? ¿algún sesgo de medición?). Incorporar P0s si los hay; re-correr si tocan el harness.
- [ ] T014 Informe final a Hernán (chat) con el veredicto GO/GO-cond/NO-GO y el ranking de gaps.
