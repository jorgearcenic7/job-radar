# ADR 0002: Matching versionado y separación entre clasificación y persistencia

## Status

Accepted

## Context

El matching de Job Radar es un conjunto de heurísticas explícitas para un
perfil de Data Engineering: ubicación, nivel, experiencia, familia del rol y
señales técnicas. Esas reglas evolucionan a medida que aparecen falsos
positivos o negativos.

Antes de esta decisión:

- `save_jobs()` en `job_radar/storage/postgres.py` importaba y ejecutaba
  `classify()` e `infer_countries()`, de modo que persistir una oferta
  implicaba clasificarla;
- el resultado de la clasificación se guardaba solo como texto humano en
  `match_reason`;
- no quedaba registrado con qué versión de las reglas se había tomado cada
  decisión.

Con ese diseño era difícil auditar por qué una oferta se había seleccionado,
agregar resultados por motivo o comparar el efecto de un cambio de reglas, y
los tests dependían de la redacción del texto.

## Decision

**Versionar las reglas.** `MATCH_RULES_VERSION` vive únicamente en
`job_radar/matching/rules.py` y se incrementa manualmente con cada cambio que
altere decisiones de matching.

**Resultado tipado.** `classify()` devuelve un `MatchResult` o `None`. El
resultado contiene `status`, `level`, `reason`, `reason_codes` y
`rules_version`.

**Reason codes estables.** `MatchReasonCode` enumera motivos machine-readable
(por ejemplo `CORE_DATA_ROLE` o `THREE_YEARS_STRETCH`). No contienen texto
dinámico ni localizado y no se reutilizan con otro significado.

**Texto humano separado.** `match_reason` sigue siendo la explicación legible
y puede cambiar de redacción. La metadata machine-readable se persiste aparte
en `jobs.match_rules_version` y `jobs.match_reason_codes`, añadidas por
`sql/005_add_matching_metadata.sql`.

**Preparación antes de persistir.** El flujo es:

```text
Job → prepare_job() → PreparedJob → storage
```

`prepare_job()`, en `job_radar/matching/preparation.py`, ejecuta `classify()`
una sola vez, deriva países y devuelve un `PreparedJob` que conserva el `Job`
original intacto. El runner prepara cada oferta una vez y usa esos valores
tanto para las métricas como para `save_jobs()`.

**Storage no depende de matching.** `save_jobs()` acepta `list[PreparedJob]`,
no deriva campos ni clasifica, y `job_radar/storage/` no importa
`job_radar.matching`.

## Consequences

### Positivas

- **Auditabilidad.** Cada oferta clasificada registra la versión de reglas y
  los motivos que produjeron su decisión.
- **Análisis por reason code.** Es posible agregar coincidencias por motivo sin
  interpretar texto libre.
- **Comparación de reglas.** La versión permite distinguir resultados de reglas
  distintas y evaluar el impacto de un cambio.
- **Base para feedback.** Una valoración posterior de una oferta puede
  relacionarse con la versión y los motivos exactos que la seleccionaron.
- **Responsabilidades más limpias.** Matching decide, storage persiste y el
  runner coordina; cada parte se prueba por separado.
- **Tests menos acoplados al texto.** Las aserciones pueden usar reason codes
  en lugar de la redacción de `match_reason`.

### Trade-offs

- **Disciplina de versión.** Todo cambio funcional del matching debe
  incrementar `MATCH_RULES_VERSION`; olvidarlo mezcla resultados de reglas
  distintas bajo la misma versión.
- **Históricos incompletos.** Las filas anteriores a la migración `005` tienen
  `match_rules_version` y `match_reason_codes` a `NULL` hasta que la oferta
  vuelve a verse y se reprocesa. Las ofertas rechazadas por `classify()` también
  las mantienen a `NULL`.
- **Contrato estable.** Los reason codes pasan a ser un contrato
  machine-readable: añadir uno es barato, pero renombrarlo, eliminarlo o
  cambiar su significado afecta a análisis existentes y debe hacerse con
  cuidado.
