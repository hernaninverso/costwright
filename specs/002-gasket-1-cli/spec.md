# Feature Specification: gasket v0.1 — el wedge OSS (CLI certificador de presupuesto)

**Feature Branch**: `002-gasket-1-cli`
**Created**: 2026-06-12
**Status**: Draft (pre-council 1 ronda — el diseño hereda del experimento 001, consenso 6/6)
**Input**: "CLI OSS que emite certificado de presupuesto para workflows LangGraph/CrewAI/AgentsSDK
reciclando el mapper validado del experimento 001; feature 1 = inferencia de caps; salida
pretty+JSON+exit codes CI; packaging pip; lanzar junto al paper"

## Contexto

El experimento 001 (GO fuerte: 76.4% mapeable, 88.1% runaway-dependiente-de-default, 11.9%
certifiability) validó el frontend estático. El paper (`~/typed-resources-paper`, Lean machine-checked)
es el respaldo formal y el canal de distribución — **gasket se lanza JUNTO al paper** (decisión de
Hernán 2026-06-12). El negocio (certificación firmada, feed de billing, capa org) queda FUERA del
OSS — esta spec construye SOLO el wedge. Licencia Apache-2.0, repo público `hernaninverso/gasket`.

## Decisiones heredadas (no re-discutir; provienen del consenso 001 D1–D8)

- Unidad = graph unit; análisis 100% estático (jamás ejecuta código del repo del usuario).
- Bound global: `n·Σ(nodos)` default, `n·max` con cadena lineal probada; Send → no certificable.
- Taxonomía de 5 categorías; defaults verificados D8 (LangGraph 1000/25, CrewAI 20, SDK 10).
- Conservadurismo > cobertura: ante duda, "no certificable con motivo", jamás certificado inflado.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - `gasket check .` emite el veredicto (Priority: P1)

Un dev con un repo LangGraph corre `pip install gasket && gasket check .` y en <30s ve, por cada
graph unit: categoría, **la cota real en supersteps y su fuente** (explícita / default del framework
/ ausente), y el hallazgo estrella cuando aplica: *"tu workflow depende del default de LangGraph:
1000 supersteps antes de frenar — cota efectivamente vacua"*.

**Why this priority**: es el wedge entero; sin esto no hay producto ni captura del pico del paper.

**Independent Test**: correr `gasket check` sobre 3 repos del dataset congelado de 001 reproduce
las categorías de results.json.

**Acceptance Scenarios**:

1. **Given** un repo con grafos, **When** `gasket check .`, **Then** salida humana con tabla por
   unit (categoría, bound, fuente, spans) + resumen, y exit code: 0 = todo certificable con bound
   explícito; 1 = hay units dependientes-de-default o no-certificables; 2 = hay rechazos (runaway).
2. **Given** `--json`, **Then** salida JSON estable (schema versionado) para CI/tooling.
3. **Given** un repo sin grafos, **Then** "no graph units found", exit 0, sin ruido.

### User Story 2 - Inferencia de caps (`gasket caps`) (Priority: P2)

El 88.1% depende de defaults y solo 11.9% anota caps. `gasket caps .` lista cada constructor de
LLM/llamada SIN cap de tokens, con el parámetro correcto POR PROVIDER (la tabla §3.2 del paper:
`max_output_tokens` OpenAI Responses, `max_completion_tokens` Azure/Chat reasoning, `max_tokens`+
`budget_tokens` Anthropic standard, `maxOutputTokens`+`thinkingBudget` Gemini) y el snippet de fix.
`--fix` aplica el edit con un valor conservador configurable (default: NO aplica nada sin flag).

**Acceptance Scenarios**:

1. **Given** `ChatOpenAI(model=...)` sin cap, **When** `gasket caps`, **Then** sugiere el kwarg
   correcto para ese constructor con cita de la fuente (§3.2).
2. **Given** `--fix --cap 1024`, **Then** edita el archivo agregando el kwarg, idempotente
   (re-correr no duplica), y reporta el diff.
3. **Given** Anthropic interleaved/adaptive thinking o Gemini sin `thinkingBudget`, **Then** warning
   explícito: "el cap NO es techo de facturación en este modo" (la degradación del paper).

### User Story 3 - GitHub Action (Priority: P3)

`uses: hernaninverso/gasket-action@v1` corre `gasket check --json` y falla el build según política
mínima (`fail-on: reject|default-dependent|none`). README con ejemplo copy-paste.

**Acceptance Scenarios**:

1. **Given** un workflow con runaway, **Then** el job falla con el span en el log.

### Edge Cases

- Repos gigantes: límite de archivos escaneados con flag (`--max-files`, default 5000) y exclusión
  estándar (venv/node_modules/tests) — heredada de 001.
- Archivos con sintaxis rota: se reportan como no-parseables, jamás crashean el run.
- `--fix` sobre archivo modificado entre scan y fix: re-verificar hash antes de editar; si cambió,
  abortar ese fix con mensaje (no editar a ciegas).
- Unicode/encodings raros: leer errors=ignore como en 001.
- El JSON de salida NUNCA incluye código fuente del usuario completo (solo spans línea/col) — apto
  para subir a CI logs sin filtrar secretos del repo.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: `gasket check [path]` — recicla extractor/mapper de 001 (mismo núcleo, mismos tests);
  salida pretty (rich-free, stdlib only) + `--json` (schema `gasket.v1`); exit codes 0/1/2 (US1).
- **FR-002**: `gasket caps [path]` — detección de constructores LLM sin cap + sugerencia por
  provider según la tabla §3.2 VERIFICADA; `--fix` opt-in con edit idempotente y hash-check.
- **FR-003**: Packaging: `pyproject.toml`, `pip install gasket`, entrypoint `gasket`, Python ≥3.10,
  CERO dependencias runtime (stdlib only — decisión 001 D3 extendida al producto).
- **FR-004**: Tests: los 17 de 001 + e2e del CLI sobre fixtures + golden del JSON schema.
- **FR-005**: README con: el pitch medido (los números de 001 CON la disclosure obligatoria del
  audit), quickstart, ejemplo de salida, link al paper/Lean, y la separación honesta OSS vs futuro
  pago ("certification & billing-feed: coming, contact").
- **FR-006**: GitHub Action (`action.yml` composite) en el mismo repo.
- **FR-007**: El certificado OSS es informativo, NO firmado — la firma criptográfica es la capa
  paga (E1) y queda explícitamente fuera de este repo.
- **FR-008**: CI del repo: GitHub Actions corriendo tests en 3.10/3.12/3.13 + el propio gasket
  sobre sí mismo (dogfood, debe salir exit 0).

### Key Entities

- **CheckResult** (= MappingResult de 001 + presentación), **CapFinding** (constructor, provider,
  kwarg correcto, span, fix sugerido), **CertReport** (schema gasket.v1).

## Success Criteria *(mandatory)*

- **SC-001**: sobre el dataset congelado de 001, `gasket check` reproduce las 254 categorías
  exactas de results.json (cero drift con el experimento).
- **SC-002**: `gasket caps` encuentra ≥90% de los 123 constructores LLM detectados en 001 y NO
  sugiere kwargs incorrectos para los 4 providers de la tabla (validado contra fixtures por provider).
- **SC-003**: `pip install` desde sdist limpio en venv nuevo funciona; `gasket check` corre <30s
  sobre el dataset entero de 001.
- **SC-004**: audit-3 (gate MÁXIMA) sin P0 antes de publicar el repo.

## Assumptions

- El repo público se crea recién DESPUÉS del audit-3 (FR de proceso, no de producto).
- El naming "gasket" en PyPI: verificar disponibilidad ANTES de fijar el nombre del package; si está
  tomado → `gasket-check` como package con CLI `gasket` (decisión técnica mía, no re-preguntar).
- La E1 (certificación firmada) NO está en scope; solo se deja el hook (schema con campo
  `signature: null` reservado).

## Council gate (1 ronda, 2026-06-12): 4/4 approve_with_changes — P0s INCORPORADOS

1. **Exit codes (REEMPLAZA US1.1)**: `gasket check` default = **exit 0 SIEMPRE que el tool corra**
   (hallazgos = warnings ruidosos en stderr+salida). Política de fallo OPT-IN:
   `--fail-on reject|default-dependent|non-certifiable` → exit 1 si la política se viola.
   **Exit 2 = error de infraestructura del CLI** (path inválido, crash interno), nunca severidad.
   La GitHub Action SÍ defaultea `fail-on: reject` (CI explícito es otro contexto).
2. **`--fix` ELIMINADO; reemplazo: `gasket caps --patch`** emite unified diff a stdout/archivo
   (`git apply` lo consume). NUNCA edita archivos del usuario. (AST-editing seguro zero-dep es
   inviable; un patch es inspeccionable y componible con Black/Ruff.)
3. **Schema `gasket.v1` CONGELADO**: `{schema:"gasket.v1", units:[{unit_id(hash estable),
   file, span:{line}, framework:enum, category:enum[certifiable|default_dependent|non_certifiable|
   runaway|parse_error], bound:{supersteps:int|null, aggregation:enum[max|sum]|null,
   provenance:enum[explicit|framework_default|absent]}, reasons:[str]}], summary:{...},
   signature:null}` — enums cerrados, campos nuevos solo aditivos en v2.
