# Feature Specification: Experimento de cobertura — grafos de agentes reales → cálculo de potencial

**Feature Branch**: `001-experimento-cobertura-mapear`
**Created**: 2026-06-12
**Status**: v2 — post-council ronda 1 (6 frontera: 3 redesign + 3 approve_with_changes; P0s incorporados abajo)
**Input**: User description: "Experimento de cobertura: mapear grafos LangGraph/CrewAI públicos reales al cálculo de potencial tipado y medir viabilidad del frontend del linter"

## Contexto (lo que NO es esta feature)

El teorema ya existe y está mecanizado (`~/typed-resources-paper/lean/`): well-typed ⟹ gas ≤ potencial,
bajo el axioma de cap. El cálculo tiene 7 construcciones: `call(c) | tool(c) | skip | seq | if |
loop(n,e) | delegate(q,e)`. La hipótesis de producto es un **linter estático** ("certificador de
presupuesto") sobre grafos de agentes existentes. **El riesgo decisivo no es el teorema — es el
frontend**: ¿el código real se deja mapear a las 7 construcciones con cobertura suficiente? Esta
feature construye el **harness del experimento** y emite un veredicto go/no-go. No construye el linter.

## Decisiones de diseño fijadas por el council (ronda 1)

- **D1 — Unidad de análisis = GRAFO, no archivo** [gpt-5.5, opus]. Un archivo puede tener 0..N grafos;
  un grafo puede armarse cross-file. La unidad es el *graph object* (un `StateGraph` compilado / un
  `Crew` / un entrypoint `Runner` del Agents SDK). El harness construye el mapeo grafo→archivo(s) y
  dedupea a nivel grafo. Todo porcentaje de SC se computa sobre grafos únicos. Grafos cuyo armado
  cruza archivos que el AST no resuelve → categoría `extractor-limit` (medida, no silenciada).
- **D2 — `recursion_limit` NO se mapea a `loop(n,e)` por-loop** [unánime R1; bound corregido en R2
  por gpt-5.5]. Semántica real **verificada contra docs** (docs.langchain.com/oss/python/langgraph/
  graph-api, jun-2026): cap GLOBAL de **supersteps** de toda la ejecución, seteado en runtime config
  (`invoke/stream(config={"recursion_limit": n})`), lanza `GraphRecursionError`. **Un superstep puede
  ejecutar VARIOS nodos en paralelo** ("if a node has multiple outgoing edges, ALL of those
  destination nodes will be executed in parallel as part of the next superstep") ⟹ el bound
  `n·max(nodo)` es **unsound** con fan-out estático. Mapeo sound (presupuesto global):
  **`n · Σ(costo de todos los nodos)`** por defecto (conservador, siempre sound); refinar a
  `n · max(nodo)` SOLO si el extractor prueba single-active-node-per-superstep (cadena lineal sin
  fan-out). Con `Send` (fan-out dinámico, N en runtime) ⟹ NO acotable estáticamente ⟹
  `no-mapeable:send-fanout`. El extractor busca `recursion_limit` en call-sites de
  invoke/stream/batch y `RunnableConfig`, no solo en la construcción.
- **D3 — Estático por decisión de producto, documentado** [responde al redesign de gemini]. El linter
  de producto corre en CI sin ejecutar código del repo (esa es su gracia frente a `get_graph()` y
  cualquier extracción dinámica, que ejecutan top-level code de terceros). Por lo tanto el
  experimento mide **cobertura estática** — exactamente la pregunta de negocio. Consecuencia
  aceptada: si la cobertura estática es baja, el resultado del experimento ES "el linter necesita
  otra arquitectura (p.ej. extracción dinámica sandboxed opt-in)" — un NO-GO informativo, no un
  fracaso del experimento.
- **D4 — Taxonomía = árbol de decisión estricto, categorías mutuamente excluyentes** [gpt-5.5, qwen,
  deepseek]. Por grafo único, en este orden:
  1. `extractor-failure` — el AST no pudo reconstruir el grafo (cross-file no resuelto, construcción
     dinámica de nodos, API no soportada). *Falla nuestra o límite del approach estático; medida.*
  2. `no-mapeable:<feature>` — el grafo se reconstruyó pero usa una construcción sin contraparte en
     el cálculo (`send-fanout`, `interrupt/human-in-loop`, `dynamic-goto` no enumerable,
     `hierarchical-manager` CrewAI, `subgraph-dinámico`, …). *Gap real del cálculo; ranking en US3.*
  3. `rechaza-con-razon` — mapea, y el tipo lo rechaza por loop/driver genuinamente no acotado
     (`while True` externo, `recursion_limit` explícitamente enorme/infinito, CrewAI sin
     `max_iter`+sin default aplicable). *Value-prop del runaway.*
  4. `tipa:explicit` — mapea y tipa con bounds 100% explícitos en el código.
  5. `tipa:framework-default` — mapea y tipa solo gracias a defaults del framework (recursion_limit
     25, max_iter por defecto de CrewAI, max_turns default). *Separado de 4 SIEMPRE.*
  Métricas ORTOGONALES a la taxonomía (se reportan aparte, nunca se mezclan en el denominador):
  - **certifiability**: % de `call/tool` con cap de tokens finito anotado o inferible
    (`max_tokens`/`max_output_tokens`/`max_completion_tokens`/`budget_tokens`). Un grafo puede
    `tipa:explicit` en estructura con todos los caps en `⊤` — estructura y certificabilidad son
    dimensiones distintas [gpt-5.5 P0].
- **D5 — Falso-rechazo medido sobre TODOS los rechazos + muestra estratificada, no N=10** [unánime:
  N=10 da CI ±30%, inútil]. Revisión manual: el 100% de `rechaza-con-razon` y `no-mapeable` (se
  espera ≤30 grafos) + 10 de `tipa:*` al azar (seed fija) para falsos-aceptados. Definición precisa
  [qwen]: *falso-rechazo* = grafo clasificado 3 que un humano leyendo el código determina acotado
  (bound real que el extractor no vio); *falso-aceptado* = clasificado 4/5 cuyo bound real no existe.
- **D6 — Dataset anti-tutorial-bias** [unánime]: (a) filtros de repo: ≥10 estrellas O presencia de
  CI/tests (`.github/workflows`, `tests/`), commit en los últimos 12 meses; (b) exclusión por
  nombre/desc: `awesome|tutorial|example|template|course|quickstart|demo` (se loguean las
  exclusiones); (c) dedup ESTRUCTURAL: hash del AST normalizado (identificadores canónicos, sin
  literales), no hash de bytes [deepseek: los clones renombran variables]; (d) estratificación por
  estrellas (0–10 / 11–100 / >100) con conteos mínimos en los buckets altos; (e) el manifest registra
  repo/path/SHA/stars/buckets y los descartes con motivo.
- **D7 — Veredicto: umbrales pre-registrados + distribución completa** [grok pedía solo distribución;
  opus pedía mantener umbrales sobre la unidad correcta — se hacen las dos cosas]. Los umbrales de SC
  se evalúan mecánicamente sobre grafos únicos; el informe SIEMPRE incluye además la distribución
  completa por categoría y el ranking de gaps, para que el veredicto sea auditable.
- **D8 — Defaults del framework: tabla VERIFICADA con cita** (como §3.2 del paper; verificado
  2026-06-12 contra fuentes primarias):

  | Framework | Parámetro | Default | Semántica | Fuente |
  |---|---|---|---|---|
  | LangGraph ≥1.0.6 | `recursion_limit` | **1000** | supersteps globales; `GraphRecursionError`; multi-nodo por superstep | docs.langchain.com graph-api: "Starting in version 1.0.6, the default recursion limit is set to 1000 steps" |
  | LangGraph <1.0.6 | `recursion_limit` | 25 | ídem | ídem (histórico) |
  | CrewAI | `max_iter` (por Agent) | **20** | "Maximum iterations before the agent must provide its best answer"; `Process.hierarchical` agrega re-delegación por manager [grok] | docs.crewai.com/concepts/agents |
  | OpenAI Agents SDK | `max_turns` (Runner.run) | **10** | `DEFAULT_MAX_TURNS=10` (run.py); `MaxTurnsExceeded`; `max_turns=None` lo DESACTIVA; wrappers `while` lo eluden [deepseek] | código fuente openai-agents-python + docs running_agents |

  **Implicación del default=1000**: el "fuel implícito del framework" en LangGraph moderno da una
  cota ~40× más laxa que la histórica (25) — `tipa:framework-default` con n=1000 produce
  certificados casi vacuos. El harness reporta la cota numérica resultante, no solo la categoría, y
  el extractor intenta detectar la versión de langgraph (lockfile/requirements) — si no puede,
  reporta ambas cotas.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Veredicto de viabilidad del frontend con dataset real (Priority: P1)

Como dueño de la decisión (Hernán), quiero la distribución de cobertura medida sobre ≥50 GRAFOS
únicos reales (unidad D1, dataset D6), con veredicto mecánico contra umbrales pre-registrados (D7),
para decidir construir / re-diseñar / no construir el wedge.

**Why this priority**: gate de toda la inversión siguiente.

**Independent Test**: `python3 run_experiment.py` sobre el dataset congelado reproduce
`results.json` + `report.md` byte-idénticos (seed fija).

**Acceptance Scenarios**:

1. **Given** el dataset congelado (≥50 grafos únicos post-dedup-estructural, provenance completa),
   **When** corre el harness, **Then** cada grafo cae en exactamente UNA categoría del árbol D4 con
   evidencia (spans de código → término del cálculo, o motivo preciso).
2. **Given** los resultados, **When** se computan métricas, **Then** el informe reporta: distribución
   completa por categoría, % mapeable (3+4+5), % certifiable (D4-ortogonal), falso-rechazo y
   falso-aceptado según D5, y el veredicto mecánico contra SC.
3. **Given** dos corridas sobre el mismo dataset, **Then** los números son idénticos (FR-007).

### User Story 2 - Value-prop del runaway medido (Priority: P2)

Como autor del producto, quiero saber qué fracción de grafos reales depende de un default del
framework o carece de bound (categorías 3 y 5 de D4, y la fuente de cada bound), para calibrar el
pitch "rechazo ex-ante" con base empírica.

**Independent Test**: por grafo con ciclo/driver, el harness emite `bound_source ∈ {explicit,
framework-default, absent}` + valor + span.

**Acceptance Scenarios**:

1. **Given** un grafo con `recursion_limit` explícito en el invoke-config, **Then** mapeo D2
   (presupuesto global) y `tipa:explicit`.
2. **Given** un grafo con ciclo sin límite explícito (default 25 aplica), **Then** `tipa:framework-default`.
3. **Given** un driver `while True` externo al framework que re-invoca el grafo, **Then**
   `rechaza-con-razon` con el span.

### User Story 3 - Ranking de gaps del cálculo (Priority: P3)

Como autor del roadmap (§8), quiero el ranking por frecuencia de las features que caen en
`no-mapeable:<feature>` y `extractor-failure:<motivo>`, con ejemplo (repo+línea) cada una, para
priorizar extensiones del cálculo (¿`par`/Send primero? ¿interrupts?) y citarlo en paper/grant.

**Acceptance Scenarios**:

1. **Given** los grafos de categorías 1–2, **Then** el informe muestra top-N de motivos con conteo y
   ejemplo navegable.

### Edge Cases

- Archivo con 0 grafos (import de test, scanner de seguridad que menciona StateGraph, README
  embebido): no entra al denominador — se detecta porque ningún *graph object* se construye [D1].
- Archivo con N>1 grafos: N unidades separadas [D1].
- Grafo armado en función/builder con nodos importados de otro módulo del MISMO repo: el extractor
  intenta resolución intra-repo de 1 salto; si no, `extractor-failure:cross-file` [deepseek P0].
- `add_conditional_edges` con dict literal de rutas → `if` enumerado (mapea); con función que
  retorna strings computados o `Send` → `no-mapeable:dynamic-goto`/`send-fanout` [council].
- `Command(goto=...)` con literal → edge estático; computado → `dynamic-goto` [grok, qwen].
- Tutorial clones con variables renombradas → los caza el dedup estructural D6c.
- Caps ausentes → certifiability aparte (D4), jamás cuenta como fallo estructural.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Recolectar grafos de repos públicos (LangGraph + CrewAI; Agents SDK si el tiempo da)
  hasta lograr **≥50 grafos únicos post-dedup-estructural** que pasen los filtros D6; congelar
  dataset + manifest (repo/path/SHA/stars/bucket) en disco; loguear descartes con motivo.
- **FR-002**: Extractor 100% AST-estático (sin ejecutar código de terceros — decisión D3) que emite
  por archivo los *graph objects* (D1) con: nodos, edges (estáticos/condicionales/dinámicos), ciclos,
  bounds con fuente (D2/D8: invoke-configs incluidos), caps de tokens, features no soportadas.
- **FR-003**: Mapper al cálculo según D2/D4: árbol de decisión estricto, una categoría por grafo,
  evidencia o motivo registrado. El mapeo de presupuesto global (D2) genera la cota
  **`n·Σ(costos de todos los nodos)`** por defecto; refina a `n·max(nodo)` SOLO con prueba de cadena
  lineal (single-active-node); `Send` queda `no-mapeable:send-fanout`.
- **FR-004**: Métricas + veredicto mecánico (D7) en `results.json` (por-grafo) y `report.md`
  (distribución, certifiability, ranking de gaps, veredicto).
- **FR-005**: Paquete de revisión manual (D5): TODOS los rechazos/no-mapeables + 10 `tipa:*` (seed
  fija), volcados lado-a-lado (código resaltado → término/motivo) en `review/` para el humano; el
  informe deja los campos falso-rechazo/falso-aceptado para completar tras la revisión.
- **FR-006**: Umbrales pre-registrados en esta spec (SC); prohibido moverlos post-datos. El informe
  reporta umbrales Y distribución completa (D7).
- **FR-007**: Determinismo: dataset congelado + seed fija ⟹ resultados byte-idénticos.
- **FR-008**: Tabla de defaults de framework con cita a docs (D8), verificada al implementar — los
  valores que el mapper usa salen de esa tabla, no de memoria.

### Key Entities

- **GraphUnit** (D1): graph object único; provenance, archivos involucrados, framework, hash
  estructural.
- **ExtractionResult**: por GraphUnit — nodos/edges/ciclos, bounds (valor+fuente), caps, features.
- **MappingResult**: categoría D4 + término del cálculo o motivo; evidencia (spans).
- **ExperimentReport**: distribución, certifiability, gaps ranking, veredicto vs SC.

## Success Criteria *(mandatory)*

### Measurable Outcomes (PRE-REGISTRADOS; unidad = grafo único; evaluación mecánica)

- **SC-001 (GO fuerte)**: mapeable (cat. 3+4+5) ≥60% Y falso-rechazo (D5, sobre todos los rechazos)
  ≤10% ⟹ construir el wedge OSS como está diseñado (estático).
- **SC-002 (GO condicional)**: mapeable 30–60% ⟹ construir sólo si el top-2 del ranking de gaps es
  implementable en <1 semana; si no, re-diseñar el frontend.
- **SC-003 (NO-GO)**: mapeable <30% O falso-rechazo >25% ⟹ no construir sobre este frontend; el
  resultado se documenta para §8/paper (gap-ranking = roadmap empírico).
- **SC-004 (value-prop runaway)**: (cat. 3 + cat. 5) / (grafos con ciclo o driver) ≥20% ⟹ el pitch
  "rechazo ex-ante / dependés de un default" tiene base; <20% ⟹ reposicionar al certificado de costo.
- **SC-005 (certifiability, informativo sin umbral)**: % de call/tool con cap finito anotado. Si ≲5%,
  el wedge necesita inferencia/inyección de caps como primera feature (resultado accionable).

## Assumptions

- gh CLI autenticado; recolección por code-search multi-query + clone selectivo de repos que pasen
  filtros D6. Si el rate-limit pega, se reduce el alcance, jamás los filtros.
- Python 3.13 stdlib `ast`; sin LLM en el loop de medición (LLM solo para explorar, nunca produce el
  número).
- Los defaults D8 se verifican contra docs oficiales al implementar (FR-008).
- Esta feature NO incluye: construir el linter, publicar repo, tocar el paper.

## Council gate

- **Ronda 1 (2026-06-12, 6 frontera vía OpenRouter)**: gpt-5.5 redesign · gemini-3.1-pro redesign ·
  grok-4.3 redesign · opus-4.8 approve_with_changes · qwen3-max approve_with_changes · deepseek-r1
  approve_with_changes. P0s consolidados → decisiones D1–D8 de esta v2. Raws: `/tmp/council_spec/`.
- **Ronda 2 (2026-06-12, los 3 redesign sobre v2)**: gemini-3.1-pro **approve** · grok-4.3
  **approve** · gpt-5.5 redesign con 2 P0 residuales: (a) `n·max` unsound por multi-nodo-por-
  superstep → corregido a `n·Σ` en D2 (verificado contra docs: el fan-out estático es real);
  (b) tabla D8 sin defaults citados → completada y verificada (hallazgo: default moderno = 1000,
  no 25). Raws: `/tmp/council_spec2/`.
- **Ronda 3 (2026-06-12, solo gpt-5.5)**: **approve_with_changes** — D2/D8 resueltos en sustancia;
  único cambio: FR-003 conservaba el `n·max` stale → corregido a `n·Σ` (aplicado). Raws:
  `/tmp/council_spec3/`.
- **CONSENSO CERRADO**: 6/6 (gemini, grok: approve · opus, qwen, deepseek, gpt-5.5:
  approve_with_changes con TODOS los cambios aplicados). Gate de council superado → implementar.
