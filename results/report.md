# Experimento de cobertura — resultados (seed 20260612)

## Dataset
- 254 graph units únicos (post dedup estructural) de 45 repos públicos filtrados (D6).
- Por framework: {"crewai": {"tipa": 18, "no-mapeable": 4}, "langgraph": {"tipa": 146, "extractor-failure": 25, "no-mapeable": 30, "rechaza-con-razon": 3}, "agents_sdk": {"tipa": 27, "extractor-failure": 1}}

## Distribución (taxónomo D4)
| Categoría | n | % |
|---|---|---|
| tipa:framework-default | 177 | 69.7% |
| extractor-failure | 26 | 10.2% |
| no-mapeable:interrupt-human-in-loop | 18 | 7.1% |
| tipa:explicit | 14 | 5.5% |
| no-mapeable:send-fanout | 6 | 2.4% |
| no-mapeable:hierarchical-manager | 5 | 2.0% |
| no-mapeable:dynamic-goto | 4 | 1.6% |
| rechaza-con-razon | 3 | 1.2% |
| no-mapeable:subgraph-node | 1 | 0.4% |

## Métricas pre-registradas
- **Mapeable (cat 3+4+5): 76.4%** → SC-001 cobertura: PASS (GO fuerte)
- **Runaway value-prop (SC-004): 88.1%** de los 42 units cíclicos dependen de un default o carecen de bound → PASS ≥20%
- **Certifiability (SC-005): 11.9%** de units con LLM tienen TODOS los calls con cap finito (50/123 constructores con cap).
- **Hallazgo D8**: 135 units "tipan" con cota ≥1000 supersteps (default moderno de LangGraph) — certificado numéricamente vacuo.

## Gaps ranking (US3 — roadmap empírico del cálculo)
- interrupt-human-in-loop: 19
- extractor:unresolved-bound: 16
- extractor:all-nodes-dynamic: 10
- send-fanout: 9
- hierarchical-manager: 5
- dynamic-goto: 4
- subgraph-node: 2

## Veredicto
- Cobertura: **GO-fuerte(cobertura)**
- Final: PENDIENTE F4 (falso-rechazo manual) — cobertura: GO-fuerte(cobertura)
- Falso-rechazo/aceptado: completar en `review/` (D5: 63 rechazos/no-mapeables + 10 tipa muestreados).

## Revisión manual D5 (completada 2026-06-12)
- **Iteración 1**: 6 rechazos revisados → 3 falsos (u087 `max_turns=settings.X` tomado como None;
  u082/u229 REPLs interactivos con `input()` tomados como runaway) + 17 `all-nodes-dynamic` con
  patrón legítimo `add_node(fn)` → **5 bugs del harness arreglados** (None-literal vs variable;
  REPL≠runaway; add_node(fn) 1-arg; NodeInterrupt; subgraph-node) + 3 tests nuevos c/u. Re-corrido.
- **Iteración 2 (final)**: 3 rechazos restantes = benchmarks internos del repo langchain-ai/langgraph
  (`recursion_limit=20_000_000_000`) → rechazos CORRECTOS (units no-representativas, anotadas).
  **Falso-rechazo: 0/3 (0%)**. Muestra tipa N=10: u135 (NodeInterrupt) corregido en iter-1;
  **falso-aceptado residual: 1/10** (u113: subgraph pasado por variable — indetectable sin
  data-flow local; limitación documentada del approach estático v1).
- **Veredicto final: GO FUERTE** — SC-001 PASS (76.4% ≥60% Y falso-rechazo 0% ≤10%);
  SC-004 PASS (88.1% ≥20%); SC-005: 11.9% ⟹ la primera feature del wedge debe ser
  inferencia/inyección de caps de tokens.

## Caveats de validez (para el audit)
- 3 frameworks; LangGraph domina (204/254 units) — refleja el ecosistema pero CrewAI/SDK tienen n chico.
- Dataset de GitHub público con filtros anti-tutorial; los workflows privados de producción pueden
  diferir (sesgo de visibilidad inherente, mitigado por stars/CI/recency, no eliminado).
- Detección de versión de langgraph por repo no implementada: se asume default moderno (1000);
  con el legacy (25) el hallazgo de vacuidad se atenúa pero el de dependencia-del-default no.

## Audit de resultados (F5 — 6 frontera, 2026-06-12): 6/6 `yes_with_caveats`
- 6/6: re-run tras bugfix de extractor = LEGÍTIMO (fixes de instrumento con tests, umbrales intactos).
- 6/6: veredicto GO-fuerte SOPORTADO por los datos.
- **Disclosure OBLIGATORIA en cualquier claim público de estos números**:
  1. LangGraph domina el dataset (204/254 = 80%) — el veredicto es primariamente un veredicto
     LangGraph; CrewAI (n=22) y Agents-SDK (n=28) están sub-potenciados.
  2. Clustering: 254 units de 41-45 repos (~5.6/repo). Cobertura **repo-weighted: 78.2%** vs
     unit-weighted 76.4% — consistentes, el clustering no altera el veredicto. Top clustering:
     didilili (38), FareedKhan (34), UiPath (31), aws-samples (22), AgentOps (20).
  3. Versión de LangGraph ASUMIDA moderna (default 1000) sin detección per-repo.
  4. Sesgo de visibilidad: GitHub público filtrado ≠ workflows privados de producción.
  5. Falso-rechazo 0% es sobre N=3 — por regla-de-tres el IC 95% llega a ~63%; NO citar "0%" pelado.
  6. Falso-aceptado residual 1/10 (subgraph-por-variable; límite documentado del estático v1).
