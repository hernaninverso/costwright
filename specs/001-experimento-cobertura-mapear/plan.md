# Implementation Plan: Experimento de cobertura — grafos reales → cálculo de potencial

**Branch**: `001-experimento-cobertura-mapear` | **Date**: 2026-06-12 | **Spec**: ./spec.md
**Input**: Feature specification from `/specs/001-experimento-cobertura-mapear/spec.md`

## Summary

Harness 100% estático que: congela un dataset de ≥50 *graph units* reales (LangGraph/CrewAI/Agents
SDK) desde los 45 repos ya clonados con filtros D6; extrae estructura por AST (nodos, edges, ciclos,
bounds con fuente, caps); mapea al cálculo de 7 construcciones según D2/D4 (presupuesto global
`n·Σ`, taxonomía de árbol estricto); emite `results.json` + `report.md` con distribución,
certifiability, ranking de gaps y veredicto mecánico contra SC pre-registrados; y genera el paquete
de revisión manual D5.

## Technical Context

**Language/Version**: Python 3.13 (Mac local), stdlib only (`ast`, `json`, `hashlib`, `pathlib`)
**Primary Dependencies**: ninguna externa en el loop de medición (D3/Assumptions); `gh` CLI solo en
la fase de recolección (ya ejecutada: 45 repos shallow-clonados en `dataset/repos/`)
**Storage**: archivos (dataset congelado + manifest.jsonl + results.json + report.md)
**Testing**: pytest con fixtures sintéticas por categoría D4 (un grafo de juguete por categoría) +
golden test de determinismo (FR-007)
**Target Platform**: Mac local, sin red post-congelado
**Project Type**: single project (harness experimental)
**Performance Goals**: corrida completa <60s sobre ~50-200 graph units (irrelevante, es batch)
**Constraints**: NO ejecutar código de terceros (D3); determinismo byte-idéntico (FR-007); umbrales
inmutables post-datos (FR-006)
**Scale/Scope**: ~45 repos, ~300 archivos candidatos, objetivo ≥50 graph units únicos

## Constitution Check

Sin constitución formal en este repo (experimento 1-2 días). Principios heredados del ecosistema que
aplican como gates: (1) sin LLM en el loop de medición; (2) conservadurismo > cobertura (ante duda,
clasificar como fallo del extractor, jamás inflar "mapeable"); (3) provenance y descartes SIEMPRE
registrados; (4) los números del informe salen del JSON, nunca de un contador de log [gotcha
toga-escala-a]. PASS.

## Project Structure

### Documentation (this feature)

```text
specs/001-experimento-cobertura-mapear/
├── spec.md              # v2 post-council (6/6 consenso)
├── plan.md              # este archivo
└── tasks.md             # fases T1-T7
```

### Source Code (repository root)

```text
harness/
├── freeze.py        # T2: barre dataset/repos/, detecta graph units (D1), dedup estructural (D6c),
│                    #     aplica filtros, escribe dataset/frozen/ + dataset/frozen_manifest.jsonl
├── extract.py       # T3: AST por graph unit → ExtractionResult (nodos/edges/ciclos/bounds/caps)
├── mapper.py        # T4: árbol D4 → MappingResult (término del cálculo o motivo); bounds D2/D8
├── metrics.py       # T5: agregación, SC mecánicos, ranking de gaps
├── review_pack.py   # T6: paquete de revisión manual D5 (review/*.md lado-a-lado)
└── run_experiment.py# orquesta T3→T6 sobre frozen/ con seed fija
dataset/
├── repos/           # 45 repos shallow (YA recolectados; input de freeze)
├── repos_manifest.jsonl
└── frozen/          # dataset congelado (output de freeze; el experimento corre SOLO sobre esto)
tests/
└── test_harness.py  # fixtures sintéticas por categoría D4 + golden determinismo
results/
├── results.json
├── report.md
└── review/
```

**Structure Decision**: single project; el harness es desechable-pero-auditable (el mapper se
recicla luego como núcleo del linter si hay GO).

## Fases

- **F0 (hecho)**: recolección D6 → 45 repos con material (`dataset/repos_manifest.jsonl`).
- **F1 freeze**: detección de graph units + dedup estructural + congelado. Gate: ≥50 units únicos;
  si <50, ampliar recolección (más repos), NUNCA aflojar filtros.
- **F2 extract+map**: AST → extracción → mapeo D2/D4. Gate: los tests sintéticos por categoría pasan.
- **F3 measure**: métricas + veredicto mecánico + review pack. Gate: determinismo verificado.
- **F4 revisión manual (humano+yo)**: completo la revisión D5 yo mismo leyendo el código (soy el
  "humano" de primera pasada; los casos dudosos van marcados para Hernán).
- **F5 audit de resultados**: los 6 de frontera auditan results+report (¿el número es creíble?
  ¿el veredicto sigue de los datos?) antes de reportar a Hernán.

## Complexity Tracking

Sin violaciones: stdlib, un solo proyecto, sin servicios.
