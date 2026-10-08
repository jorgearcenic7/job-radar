# ADR 0001: Migraciones SQL versionadas sin Alembic/SQLAlchemy

## Status

Accepted

## Context

Job Radar persiste en PostgreSQL mediante SQL explícito y parametrizado con
`psycopg`. No usa ORM: `job_radar/storage/` escribe directamente las consultas
de upsert, cierre, reactivación y métricas.

El schema ya estaba descrito por ficheros `.sql` numerados en `sql/`
(`001`–`004`), y la base de producción tenía esas migraciones aplicadas
manualmente, sin registro de qué se había ejecutado ni con qué contenido.
Para operar con seguridad hacía falta:

- saber qué migraciones están aplicadas y cuáles pendientes;
- detectar si un fichero aplicado se ha modificado después;
- reproducir el mismo schema en una base nueva, en CI y en producción;
- evitar que dos procesos migren a la vez;
- incorporar una base existente sin volver a ejecutar su SQL.

Alembic, o SQLAlchemy con su sistema de migraciones, resuelve estos puntos,
pero añade dependencias, un modelo de metadatos paralelo al SQL existente y
una capa de abstracción que el resto del código no utiliza.

## Decision

Mantener las migraciones como SQL plano y añadir un migrador propio y pequeño
en `job_radar/storage/migrations.py`, invocado mediante `scripts/migrate.py`.

- **Ficheros numerados.** Cada migración es `sql/NNN_descripcion.sql`. El
  migrador valida el patrón del nombre, rechaza versiones duplicadas y exige
  UTF-8. Se aplican en orden numérico.
- **Tracking en `schema_migrations`.** Cada fila guarda `version`, `filename`,
  `checksum` y `applied_at`.
- **Checksum SHA-256.** Se calcula sobre los bytes del fichero. Un checksum o
  filename distinto al registrado, una migración registrada que falta en
  `sql/` o un hueco en el historial se tratan como drift y detienen el comando.
- **Advisory lock.** `pg_try_advisory_lock` con una clave fija impide
  ejecuciones concurrentes; un segundo migrador falla sin esperar.
- **Ejecución transaccional.** Cada migración y su fila en
  `schema_migrations` se confirman en la misma transacción: o se aplican ambas
  o ninguna.
- **Baseline explícito.** Si existen tablas pero no tracking, el modo normal se
  niega a ejecutar SQL. `--baseline VERSION` muestra qué registraría y solo
  escribe con `--confirm-baseline`. No ejecuta SQL ni inspecciona objetos, y se
  rechaza en una base vacía o con historial ya registrado. El procedimiento
  está en [OPERATIONS.md](../OPERATIONS.md#base-existente-sin-tracking).
- **Inmutabilidad.** Una migración aplicada no se edita. Todo cambio de schema
  añade un fichero nuevo con un número posterior al último existente.
- **Invocación manual.** `--status` y `--check` permiten inspeccionar el
  estado; el pipeline programado no ejecuta el migrador automáticamente.

## Consequences

### Positivas

- **Poco acoplamiento.** El migrador solo depende de la biblioteca estándar y
  de `psycopg`, que el proyecto ya usa.
- **SQL transparente.** Lo que se revisa en el PR es exactamente lo que se
  ejecuta en PostgreSQL, sin generación intermedia.
- **Pocas dependencias.** No hay ORM ni framework de migraciones que mantener,
  auditar o actualizar.
- **Despliegues reproducibles.** Una base nueva aplica la misma secuencia que
  CI; una base existente se incorpora mediante un baseline revisado.
- **Drift detectable.** `--check` falla ante migraciones pendientes, checksums
  alterados, historial incompleto o un schema sin tracking.

### Trade-offs

- **Menos funcionalidad que Alembic.** No hay ramas de migración, merges de
  heads, migraciones en Python ni integración con modelos.
- **Sin autogeneración.** Cada migración se escribe a mano a partir del cambio
  de schema deseado.
- **Rollback no automatizado.** No existen migraciones `down`. Si alguna vez
  se necesita revertir, se diseña explícitamente como una migración nueva o un
  procedimiento operativo.
- **Disciplina manual.** El equipo crea el fichero con el siguiente número, lo
  revisa y respeta la inmutabilidad; las reglas viven en
  [AGENTS.md](../../AGENTS.md) y las verifica el propio migrador.

Si estas limitaciones dejaran de ser aceptables, la adopción de otra
herramienta debe documentarse en un ADR nuevo que sustituya a este.
