# Operación y diagnóstico

## Ejecuciones disponibles

El workflow `.github/workflows/job-radar.yml`, llamado **Daily Job Radar**,
ofrece:

- `workflow_dispatch` para ejecución manual;
- cron `0 6 */2 * *` con timezone `Europe/Madrid`.

Ese cron se evalúa a las 06:00 en los días del mes seleccionados por `*/2`; no
debe describirse como una garantía exacta de 48 horas entre ejecuciones. La
concurrencia usa un único grupo y no cancela una ingesta ya iniciada. El job
tiene timeout de 30 minutos.

El workflow construye el `Dockerfile`, exige `DATABASE_URL`,
`RESEND_API_KEY` y `NOTIFICATION_EMAIL`, y pasa opcionalmente
`NOTIFICATION_FROM`. `NOTIFICATION_RUN_ID` combina run y reintento de GitHub
para la idempotencia de Resend.

### Keepalive de workflows programados

GitHub desactiva los cron de un repositorio público tras 60 días sin
actividad. `.github/workflows/keepalive.yml` se ejecuta los días 1 y 15 y
reactiva por API `job-radar.yml` y a sí mismo, lo que reinicia ese contador
sin commits artificiales. Solo necesita `permissions: actions: write` sobre el
token del propio workflow. Si alguna vez aparece desactivado, reactívalo desde
**Actions** o lanza **Workflow keepalive** manualmente.

## Variables

| Variable | Consumidor | Comportamiento comprobable |
| --- | --- | --- |
| `DATABASE_URL` | Pipeline, migrador, health, feedback; fallback web fuera de Vercel | Conexión PostgreSQL. |
| `WEB_DATABASE_URL` | Web | Obligatoria cuando `VERCEL=1`; preferida siempre. |
| `RESEND_API_KEY` | Pipeline | Sin ella la notificación local se omite; el workflow la exige. |
| `NOTIFICATION_EMAIL` | Pipeline | Requerida si hay API key y por el workflow. |
| `NOTIFICATION_FROM` | Pipeline | Opcional; fallback a `Job Radar <onboarding@resend.dev>`. |
| `NOTIFICATION_RUN_ID` | Pipeline | Añade `Idempotency-Key` al envío. |
| `TEST_DATABASE_URL` | Tests PostgreSQL | Sin ella, esos tests se omiten. |

No uses prefijo `NEXT_PUBLIC_` para URLs de base de datos. Los `.env` reales
están ignorados; solo se versionan los ejemplos.

## Preparar PostgreSQL

El repo no aprovisiona Neon ni ejecuta migraciones automáticamente. Para una
base nueva, el migrador descubre `sql/*.sql`, crea `schema_migrations` y aplica
solo las pendientes en orden:

```bash
python3 scripts/migrate.py
python3 scripts/migrate.py --status
python3 scripts/migrate.py --check
```

Cada migración y su registro se confirman en la misma transacción. La tabla
guarda versión, filename, checksum SHA-256 y `applied_at`. Un checksum distinto
o un historial incompleto detienen el comando. Un advisory lock impide dos
migradores simultáneos; si ya está ocupado, el segundo falla sin esperar.

Si no existe tracking pero el schema ya contiene tablas, el modo normal se
niega a ejecutar SQL y exige revisar si corresponde hacer baseline.

### Base existente sin tracking

La base de producción ya tiene `001`–`004` aplicadas manualmente. Tras revisar
que esas cuatro migraciones corresponden exactamente a su schema, previsualiza
la operación única:

```bash
python3 scripts/migrate.py --baseline 004
```

Ese comando muestra lo que registraría, termina con error y no modifica la
base. Solo después de revisar la lista, confirma explícitamente:

```bash
python3 scripts/migrate.py --baseline 004 --confirm-baseline
python3 scripts/migrate.py --check
```

El baseline no ejecuta `001`–`004`, no inspecciona objetos para inferir su
estado y no debe usarse en una base nueva. Registra únicamente el prefijo hasta
la versión indicada; se rechaza si no hay tablas existentes o si
`schema_migrations` ya contiene filas. Haz backup y prueba primero con una copia
restaurada de producción.

Para un servicio gestionado como Neon se necesitan, fuera del repo:

- una credencial de escritura para la ingesta;
- una credencial distinta con solo `SELECT` sobre `jobs` para la web;
- TLS, backups, rotación y límites configurados en el proveedor.

El código web solo ejecuta `SELECT`, pero no puede demostrar ni imponer que la
credencial recibida sea read-only.

## Ejecución local

Con entorno Python y `DATABASE_URL` configurada:

```bash
python3 main.py
```

Con Docker Compose:

```bash
docker compose up -d db
docker compose run --rm pipeline
```

El `Dockerfile` usa Python 3.12, instala el lock de producción y ejecuta con UID
10001. Incluye el migrador y el comando de health para invocarlos manualmente;
el entrypoint y el workflow programado no los ejecutan por separado. Compose y
GitHub Actions añaden filesystem de solo lectura, `/tmp` temporal, capacidades
eliminadas y `no-new-privileges`.

`compose.web.yaml` monta el código local y expone el dev server solo en
`127.0.0.1:3000`. Está pensado para desarrollo, no como manifiesto de
producción.

## Estados de ejecución

| Estado | Significado |
| --- | --- |
| `success` | Todas las fuentes terminaron y la notificación no falló. |
| `partial` | Una o más fuentes fallaron de forma recuperable; las demás se conservaron. |
| `failed` | Hubo fallo de notificación o error fatal del pipeline. |

Una fuente requerida fallida hace que el proceso termine con código 1 aunque
el run sea `partial`. Una fuente opcional (`required=False`; ahora no hay
ninguna configurada) que falla se registra y produce `partial`, pero el proceso
puede terminar con 0. Consulta la semántica completa en
[ARCHITECTURE.md](ARCHITECTURE.md#observabilidad-y-semántica-de-errores).

## Qué observar

`ingestion_runs` responde si el pipeline terminó y resume:

- fuentes totales, correctas y fallidas;
- ofertas vistas, nuevas y cerradas;
- coincidencias;
- timestamps y estado.

`source_runs` permite localizar la fuente problemática mediante:

- `company`, `source`, `status` y `duration_ms`;
- `jobs_seen`, `jobs_new`, `jobs_closed` y `matches`;
- `snapshot_status` y `closure_suppressed`;
- `error_type` y `error_message`.

Los logs emiten eventos `ingestion_run_started`, `source_run_finished`,
`snapshot_closure_suppressed`, `http_retry`, `notification_sent` y los errores
correspondientes. Usa `run_id` y `source_run_id` para correlacionarlos.

### Salud histórica y SLO de conectores

El estado `success`/`partial`/`failed` describe una ingesta concreta. Connector
health resume el historial reciente de cada fuente y se calcula al consultar
`source_runs`; no se persiste en una tabla adicional. `snapshot_status`
describe la integridad de un snapshot y no cuenta automáticamente como fallo
HTTP o del conector.

La ventana contiene las últimas diez ejecuciones completadas de cada fuente:

- `unknown`: no existe historial completado;
- `healthy`: el último run fue correcto y la tasa de éxito es al menos 90 %;
- `degraded`: hay un fallo reciente aislado o la tasa está entre 70 % y menos
  de 90 %;
- `unhealthy`: hay dos o más fallos consecutivos, o una ventana completa tiene
  menos de 70 % de éxito.

Una tasa baja con menos de diez muestras queda `degraded`, salvo que ya haya
dos fallos consecutivos. El SLO operativo de Job Radar —no una garantía del
proveedor externo— exige que cada fuente `required` tenga al menos 90 % de
éxito en sus últimas diez ejecuciones. Se considera incumplido con una ventana
completa por debajo del 90 % o anticipadamente con dos fallos consecutivos.
Una fuente opcional siempre queda fuera del incumplimiento global.

Aparte del health, `consecutive_empty_snapshots` cuenta las ejecuciones
correctas más recientes con snapshot `empty`. A partir de tres seguidas
(`PERSISTENT_EMPTY_SNAPSHOT_RUNS`, unos seis días con el cron actual) se
considera un snapshot vacío persistente: suele indicar que la empresa cambió
de ATS o de slug. No altera `health_status` ni el SLO.

Con `DATABASE_URL` configurada:

```bash
python3 scripts/source_health.py
python3 scripts/source_health.py --problems
python3 scripts/source_health.py --check
```

El primer comando lista todas las fuentes; `--problems` limita la salida a
`degraded`/`unhealthy` y a fuentes con snapshot vacío persistente; `--check`
termina distinto de cero si una fuente `required` incumple el SLO. Tras cada
ingesta, el correo existente incluye una sección compacta solo cuando hay
fuentes `required` `unhealthy` o con snapshot vacío persistente; no se envía
un segundo mensaje.

## Feedback de relevancia

El feedback mide qué parte de las recomendaciones mostradas resulta
relevante, por versión de reglas, estado y reason code. No mide recall. Se
gestiona solo con `DATABASE_URL`; la web sigue siendo read-only y su rol no
necesita privilegios sobre `job_feedback`.

Requiere la migración `006`. En una base existente, aplícala manualmente antes
del primer uso:

```bash
python3 scripts/migrate.py --status
python3 scripts/migrate.py
python3 scripts/migrate.py --check
```

Flujo habitual:

```bash
python3 scripts/job_feedback.py pending --limit 20
python3 scripts/job_feedback.py rate --source greenhouse --job-id 12345 --relevance relevant
python3 scripts/job_feedback.py rate --source greenhouse --job-id 12345 --relevance not_relevant
python3 scripts/job_feedback.py report
python3 scripts/job_feedback.py report --rules-version 1
```

`pending` lista ofertas activas seleccionadas sin valoración para su versión
actual, con `source` y `job_id`. `rate` valora la versión con la que la oferta
está clasificada ahora y copia su estado y reason codes; repetirlo cambia la
valoración sin duplicarla. Falla con código 1 si la oferta no existe, no fue
seleccionada o no tiene metadata versionada. `report` muestra totales y tasas
siempre junto al tamaño de muestra (`relevant/labeled`); ordena los reason
codes por número de falsos positivos.

## Diagnosticar una fuente rota

1. Ejecuta `python3 scripts/source_health.py --problems` y revisa tasa, fallos
   consecutivos y último error.
2. Localiza sus últimos `source_runs`, incluida la última ejecución correcta,
   y distingue el fallo del estado del snapshot.
3. Reproduce el endpoint con el mismo slug/tenant y User-Agent cuando aplique.
4. Comprueba status, redirects, content type y estructura antes de editar.
5. Contrasta el career site oficial: el slug o proveedor pueden haber cambiado.
6. Ejecuta el fetcher manualmente sin persistir si necesitas validar mapping.
7. Añade un test de regresión mockeado antes de corregir el código.
8. Revisa si el último snapshot suprimió cierres y que no haya cierres masivos.

Interpretación habitual:

| Respuesta | Acción |
| --- | --- |
| 401/403 | Revisa si el endpoint dejó de ser público, headers y términos; no eludas controles de acceso. |
| 404 | Confirma slug, región y URL oficial; no sustituyas por un scraper sin validar el ATS. |
| 408/429/5xx | Las utilidades comunes reintentan estados transitorios hasta tres veces; revisa `Retry-After` y disponibilidad. |
| 200 sin `jobs` o campos esperados | Trátalo como cambio de contrato, no como snapshot vacío. |
| 200 con cero ofertas | Confirma en el career site que realmente no hay vacantes y que el proveedor no acepta slugs inválidos. |

Los fallos de disponibilidad de un ATS son incidencias operativas, no
vulnerabilidades salvo que exista un impacto de seguridad independiente.

## Web en Vercel

La aplicación es compatible con despliegue Next.js en Vercel, pero el repo no
contiene `vercel.json` ni aprovisionamiento. Configura `WEB_DATABASE_URL` como
secreto server-side y usa un rol de lectura. `web/src/lib/db.ts` rechaza el fallback a
`DATABASE_URL` cuando detecta `VERCEL=1` para reducir el riesgo de usar por
accidente la credencial de ingesta.

Antes de desplegar:

```bash
cd web
npm ci
npm audit --omit=dev --audit-level=high
npm run lint
npm test
npm run build
```

La CSP usa nonce por petición y las cabeceras defensivas se configuran en el
repo. TLS, WAF, límites, logs y protección del proyecto dependen de Vercel y no
se pueden confirmar desde este código.

## Verificaciones de producción

Esta sección explica cómo comprobar lo que el repositorio no puede garantizar.
No afirma que nada de esto esté configurado: hazlo con acceso a Neon, Vercel y
GitHub, y anota la fecha de la última verificación.

### Rol de solo lectura de `WEB_DATABASE_URL`

Conéctate **con la credencial de la web** (`psql "$WEB_DATABASE_URL"`) y
ejecuta:

```sql
SELECT current_user;

-- Solo can_select debe ser true.
SELECT
    has_table_privilege(current_user, 'jobs', 'SELECT') AS can_select,
    has_table_privilege(current_user, 'jobs', 'INSERT') AS can_insert,
    has_table_privilege(current_user, 'jobs', 'UPDATE') AS can_update,
    has_table_privilege(current_user, 'jobs', 'DELETE') AS can_delete,
    has_table_privilege(current_user, 'jobs', 'TRUNCATE') AS can_truncate;

-- La web solo necesita SELECT sobre jobs.
SELECT table_name, privilege_type
FROM information_schema.role_table_grants
WHERE grantee = current_user
ORDER BY 1, 2;

-- Sin atributos elevados ni pertenencia a roles administrativos.
SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls
FROM pg_roles
WHERE rolname = current_user;

SELECT parent.rolname
FROM pg_auth_members AS membership
JOIN pg_roles AS parent ON parent.oid = membership.roleid
JOIN pg_roles AS member ON member.oid = membership.member
WHERE member.rolname = current_user;

-- Prueba negativa: debe fallar con "permission denied" sin tocar filas.
BEGIN;
UPDATE jobs SET title = title WHERE FALSE;
ROLLBACK;
```

En Neon, los roles creados desde la consola o la API pueden heredar
`neon_superuser`. Si la consulta de pertenencia lo muestra, el rol **no** es de
solo lectura: crea uno con SQL (`CREATE ROLE ... LOGIN`,
`GRANT CONNECT`, `GRANT USAGE ON SCHEMA public`, `GRANT SELECT ON jobs`) y
sustituye la variable en Vercel.

Revisa también qué otras variables de base de datos recibe el proyecto de
Vercel. La integración Neon–Vercel puede inyectar `DATABASE_URL`/`POSTGRES_*`
con el rol propietario. `db.ts` las ignora cuando `VERCEL=1`, pero siguen
presentes en el entorno de ejecución. Si no las usa nada más, elimínalas del
proyecto web.

### Backups

El repo no hace copias de seguridad. Comprueba en Neon:

- la ventana de restauración (history retention) del proyecto y del plan;
- que sabes restaurar: crea una rama desde un instante pasado, ejecuta
  `DATABASE_URL=<url de la rama> python3 scripts/migrate.py --status`, cuenta
  las filas de `jobs` y elimina la rama.

Si la ventana de Neon es corta, añade una copia lógica fuera de Neon y prueba
que se puede leer:

```bash
pg_dump --format=custom --no-owner "$DATABASE_URL" \
  --file "job-radar-$(date +%F).dump"
pg_restore --list "job-radar-$(date +%F).dump" | head
```

`job_feedback` contiene tus valoraciones; trata el volcado como dato privado.

### Monitorización

Lo que el repo ya proporciona:

- correo tras cada ingesta, con alerta de fuentes `required` `unhealthy` o con
  snapshot vacío persistente;
- código de salida 1 del workflow si falla una fuente `required` (GitHub avisa
  según la configuración de notificaciones de tu cuenta);
- `python3 scripts/source_health.py --check` para revisar el SLO a mano;
- la web marca la fecha de actualización en ámbar tras 72 horas sin datos.

Lo que no existe y conviene decidir:

- **Ingesta que no se ejecuta.** Si el workflow no arranca, no llega correo y
  nadie avisa de su ausencia. Opciones: un heartbeat externo al final del
  workflow, o revisar que llega el correo cada 2 días.
- **Disponibilidad de la web.** No hay monitor externo; Vercel muestra logs y
  errores de funciones en su panel.

### Límites externos de Vercel y Neon

Dependen del plan y no se pueden confirmar desde el código. Revisa:

- **Vercel:** duración máxima y uso de funciones, ancho de banda, reglas de
  Firewall/rate limiting disponibles y Deployment Protection.
- **Neon:** almacenamiento, horas de cómputo, autosuspend (la primera petición
  tras suspender es más lenta), límite de conexiones (la web debería usar el
  endpoint con pooler) y ventana de restauración.

Parámetros fijados en el repo: el pool de la web usa `max: 5`, y
`connectionTimeoutMillis`, `query_timeout` y `statement_timeout` de 10 s
(`web/src/lib/db.ts`). El pipeline usa `connect_timeout=10`.

### Coste por petición y rate limiting

Cada carga de la web ejecuta 6 consultas (1 de frescura y 5 en paralelo) sin
caché, y la página es dinámica. Están acotadas: filtros de 120 caracteres como
máximo, página hasta 500, SQL parametrizado y `statement_timeout` de 10 s. Aun
así, no hay rate limiting: un bot o un crawler que recorra combinaciones de
filtros consume cómputo de Neon. Un limitador en memoria en `proxy.ts` no
serviría, porque cada instancia serverless tendría su propio contador. Las
opciones sin dependencias nuevas son una regla de rate limiting en el Firewall
de Vercel para `/` y cachear las 4 consultas que no dependen de los filtros.

### Crecimiento de `jobs`

Las ofertas cerradas se conservan con su descripción. En octubre de 2026 se
cerraban unas 330–930 ofertas por ingesta (logs de Actions) y la descripción
media de Greenhouse rondaba 5,3 KB. A ese ritmo, `jobs` crece del orden de
cientos de MB al año antes de compresión. Vigila el tamaño
(`SELECT pg_size_pretty(pg_total_relation_size('jobs'));`) frente al límite
del plan. Si hace falta una política de retención, recuerda que `job_feedback`
referencia `jobs` con `ON DELETE RESTRICT`.

Los índices actuales son la clave primaria `(source, source_job_id)` e
`idx_jobs_active (active)`. Con el volumen actual, las consultas de la web
recorren casi todas las filas activas (el corte de 7 días incluye casi todas),
así que un índice adicional apenas ayudaría. Revísalo con
`EXPLAIN (ANALYZE, BUFFERS)` si `jobs` crece en un orden de magnitud.

## Resend

El correo se construye escapando campos externos y solo enlaza URLs HTTPS.
Las coincidencias vistas por primera vez en la ejecución actual aparecen en
una sección «Nuevas en esta ejecución» y su número se añade al asunto. No se
incluyen ofertas activas que lleven más de 7 días (`STALE_AFTER_DAYS`) sin
verse respecto a la última ingesta: su fuente lleva varias ejecuciones
fallando y su enlace ya no está verificado; cuántas se ocultan queda en el
log `stale_matches_hidden`. El envío reintenta errores
transitorios con la política HTTP común; siempre lleva `Idempotency-Key`
(`NOTIFICATION_RUN_ID` o un UUID por envío), así que un reintento no duplica
el correo.
Una ausencia de `RESEND_API_KEY` omite el envío; una API key sin destinatario es
error. El workflow programado exige ambas variables, por lo que no ejecuta una
ingesta de producción silenciosamente sin notificación.
