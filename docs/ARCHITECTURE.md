# Arquitectura de Job Radar

Este documento describe el comportamiento implementado en el repositorio. La
configuración de servicios externos —PostgreSQL gestionado, Neon, Vercel,
Resend o controles de cuenta de GitHub— queda fuera del código y se trata como
responsabilidad operativa.

## Componentes

```text
main.py
└── job_radar.orchestration.runner
    ├── job_radar.connectors        obtiene y normaliza snapshots
    ├── job_radar.matching          clasifica y deriva campos
    ├── job_radar.storage           persiste jobs y ejecuciones
    ├── orchestration.snapshots     decide si se pueden cerrar ausentes
    └── job_radar.notifications     envía coincidencias activas

web/
└── Server Component Next.js ───── consulta PostgreSQL directamente
```

| Módulo | Responsabilidad |
| --- | --- |
| `main.py` | Invocar `run()` y devolver su código de salida. |
| `domain/models.py` | Contrato normalizado `Job`. |
| `job_radar/connectors/registry.py` | Empresas, parámetros, orden y flags de ejecución. |
| `job_radar/connectors/ats.py` | Integraciones reutilizables con ATS. |
| `job_radar/connectors/custom.py` | Career sites que no encajan en los ATS reutilizables. |
| `job_radar/connectors/common.py` | HTTP, reintentos, HTML a texto y conversión a `Job`. |
| `matching/rules.py` | Ubicación, salario, experiencia, países y clasificación. |
| `storage/postgres.py` | Upsert, cierres, reactivación y consulta para email. |
| `storage/runs.py` | Persistencia de métricas por pipeline y fuente. |
| `job_radar/orchestration/snapshots.py` | Evaluación de snapshots vacíos o anómalos. |
| `job_radar/orchestration/runner.py` | Coordinación secuencial, estados, logs y notificación. |
| `notifications/email.py` | Render HTML/texto y llamada a Resend. |
| `web/src/app/page.tsx` | Consulta y render server-side del dashboard. |
| `sql/` | Schema y migraciones aplicadas manualmente en orden. |

## Contrato normalizado

Todo conector devuelve una lista de `Job` con estos campos:

```text
source, source_job_id, company, title, location, url,
description, salary_text, experience_text
```

`source_job_id` debe ser estable dentro de `source`. La base usa ambos campos
como clave primaria; cambiar cualquiera crea una identidad distinta. Los
conectores conservan descripción, ubicación y fragmentos publicados. Los
países, el resultado de matching y su motivo se derivan al persistir.

## Flujo de una ingesta

1. `RunRepository.create_ingestion_run()` crea un run `running` con el número
   de conectores de `CONNECTORS`.
2. El runner recorre las fuentes secuencialmente y crea un `source_run` por
   cada una.
3. El conector obtiene el snapshot. Las utilidades HTTP comunes hacen hasta
   tres intentos para timeouts, errores de conexión y HTTP 408, 429, 500, 502,
   503 o 504. Respetan `Retry-After`, aplican backoff y jitter; un 404 no se
   reintenta.
4. Se calcula `jobs_seen`, se cuentan matches y se consulta el historial de
   snapshots exitosos de esa empresa/fuente.
5. `evaluate_snapshot()` decide `healthy`, `suspicious` o `empty` y si debe
   suprimir cierres.
6. `save_jobs()` vuelve a clasificar, deriva países y ejecuta upserts. Si los
   cierres están permitidos, desactiva las identidades que faltan.
7. Se finaliza `source_run` con métricas o información del error.
8. Tras procesar fuentes recuperables, se intenta notificar las coincidencias
   activas por correo.
9. El run se finaliza como `success`, `partial` o `failed`, incluso ante una
   excepción fatal.

## Matching y datos derivados

`classify(job)` devuelve `(status, level, reason)` o `None`. Primero exige una
ubicación compatible, descarta niveles no objetivo y requisitos mínimos de
cuatro o más años, identifica la familia del rol y cuenta señales técnicas.
Los roles core pueden ser `Buena coincidencia` o `Stretch`; los roles
adyacentes aceptados siempre son `Stretch`.

`infer_countries()` carga países y ciudades desde `geonamescache`, prioriza
países o códigos ISO explícitos y usa ciudad solo como fallback. La ubicación
original nunca se reemplaza.

Los extractores de salario y experiencia son heurísticos conservadores: solo
devuelven fragmentos que aparecen en la descripción. Los conectores pueden
aportar un campo publicado por el ATS y usar los extractores como fallback.

## Persistencia y lifecycle

### `jobs`

- Clave: `(source, source_job_id)`.
- Primera aparición: inserta la oferta con `first_seen_at` y `active = TRUE`.
- Aparición posterior: actualiza contenido y matching, mueve `last_seen_at`,
  reactiva y limpia `closed_at`.
- Ausencia en un snapshot sano: marca `active = FALSE` y fija `closed_at`.
- Snapshot vacío: `save_jobs()` devuelve sin cerrar nada como segunda defensa,
  además de la protección del orquestador.

Cada fuente se guarda en su propia conexión/transacción. No existe una
transacción única para todas las fuentes.

### Snapshot protection

La evaluación usa hasta diez ejecuciones exitosas anteriores y toma como línea
base la mediana de hasta cinco snapshots `healthy` o históricos `unknown` con
ofertas.

- `jobs_seen == 0`: estado `empty`, cierres suprimidos.
- Sin línea base confiable: `healthy`.
- Se considera caída sospechosa solo si la línea base es al menos 20, faltan al
  menos 10 ofertas y el snapshot actual es menor al 50 % de la mediana.
- Un snapshot sospechoso actualiza las ofertas presentes, pero no cierra las
  ausentes.
- El nivel reducido puede estabilizarse después de tres snapshots similares;
  la tolerancia es el mayor valor entre dos ofertas y el 10 % del recuento.

Los umbrales pertenecen a `job_radar/orchestration/snapshots.py`; esta documentación no
debe sustituir su lectura antes de cambiarlos.

## Observabilidad y semántica de errores

`ingestion_runs` resume toda la ejecución. `source_runs` conserva empresa,
fuente, duración, recuentos, estado del snapshot, supresión de cierres y
detalles del error. El runner emite logs estructurados con los mismos datos.

Los conectores configurados por ATS son `required=True` y `catch_all=False`.
Los conectores personalizados son `catch_all=True`; Revolut es además la única
fuente `required=False`.

| Situación | Continúa | Estado final | Código de salida |
| --- | --- | --- | ---: |
| Todas las fuentes correctas y correo enviado u omitido sin error | Sí | `success` | 0 |
| Error recuperable en fuente requerida | Sí | `partial` | 1 |
| Error recuperable en Revolut | Sí | `partial` | 0 |
| Error inesperado no capturable | No; se finaliza el run y se relanza | `failed` | excepción |
| Error de notificación | Termina la ingesta | `failed` | 1 |

Un run puede ser `partial` aunque la fuente fallida sea opcional. El flag
`partial` del correo solo se activa cuando hubo un fallo bloqueante.

## Aplicación web

`web/src/app/page.tsx` fuerza render dinámico y ejecuta consultas parametrizadas
mediante el pool de `web/src/lib/db.ts`. Solo lee `jobs.active = TRUE`, limita
la página a 20 filas, acota filtros a 120 caracteres y la página solicitada a
500. No existe una API intermedia en el repo.

En Vercel, `WEB_DATABASE_URL` es obligatoria. El código no emite escrituras,
pero tampoco puede convertir una credencial PostgreSQL en read-only: los
privilegios se conceden fuera del repositorio.

`web/src/proxy.ts` crea un nonce por petición y envía CSP. `web/next.config.ts`
añade el resto de cabeceras defensivas. Los enlaces de ofertas se renderizan
solo si su esquema es HTTPS.

## Schema

Los SQL se aplican en orden:

1. `sql/001_create_jobs.sql`
2. `sql/002_add_job_lifecycle.sql`
3. `sql/003_add_ingestion_observability.sql`
4. `sql/004_add_snapshot_health.sql`

No hay herramienta de migración ni ejecución automática de estos ficheros. Un
entorno nuevo necesita los cuatro; un cambio de schema debe añadir una
migración nueva sin reescribir la historia aplicada.
