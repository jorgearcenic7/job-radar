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

## Variables

| Variable | Consumidor | Comportamiento comprobable |
| --- | --- | --- |
| `DATABASE_URL` | Pipeline; fallback web fuera de Vercel | Conexión PostgreSQL. |
| `WEB_DATABASE_URL` | Web | Obligatoria cuando `VERCEL=1`; preferida siempre. |
| `RESEND_API_KEY` | Pipeline | Sin ella la notificación local se omite; el workflow la exige. |
| `NOTIFICATION_EMAIL` | Pipeline | Requerida si hay API key y por el workflow. |
| `NOTIFICATION_FROM` | Pipeline | Opcional; fallback a `Job Radar <onboarding@resend.dev>`. |
| `NOTIFICATION_RUN_ID` | Pipeline | Añade `Idempotency-Key` al envío. |
| `TEST_DATABASE_URL` | Tests PostgreSQL | Sin ella, esos tests se omiten. |

No uses prefijo `NEXT_PUBLIC_` para URLs de base de datos. Los `.env` reales
están ignorados; solo se versionan los ejemplos.

## Preparar PostgreSQL

El repo no aprovisiona Neon ni ejecuta migraciones automáticamente. Aplica los
SQL versionados en orden:

```bash
psql "$DATABASE_URL" < sql/001_create_jobs.sql
psql "$DATABASE_URL" < sql/002_add_job_lifecycle.sql
psql "$DATABASE_URL" < sql/003_add_ingestion_observability.sql
psql "$DATABASE_URL" < sql/004_add_snapshot_health.sql
```

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

El `Dockerfile` usa Python 3.12, instala dependencias directas y ejecuta con UID
10001. Compose y GitHub Actions añaden filesystem de solo lectura, `/tmp`
temporal, capacidades eliminadas y `no-new-privileges`.

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
el run sea `partial`. La única fuente opcional actual es Revolut: su fallo se
registra y produce `partial`, pero el proceso puede terminar con 0. Consulta la
semántica completa en [ARCHITECTURE.md](ARCHITECTURE.md#observabilidad-y-semántica-de-errores).

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

## Diagnosticar una fuente rota

1. Localiza su último `source_run` y el error exacto.
2. Reproduce el endpoint con el mismo slug/tenant y User-Agent cuando aplique.
3. Comprueba status, redirects, content type y estructura antes de editar.
4. Contrasta el career site oficial: el slug o proveedor pueden haber cambiado.
5. Ejecuta el fetcher manualmente sin persistir si necesitas validar mapping.
6. Añade un test de regresión mockeado antes de corregir el código.
7. Revisa si el último snapshot suprimió cierres y que no haya cierres masivos.

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
npm run build
```

La CSP usa nonce por petición y las cabeceras defensivas se configuran en el
repo. TLS, WAF, límites, logs y protección del proyecto dependen de Vercel y no
se pueden confirmar desde este código.

## Resend

El correo se construye escapando campos externos y solo enlaza URLs HTTPS.
Una ausencia de `RESEND_API_KEY` omite el envío; una API key sin destinatario es
error. El workflow programado exige ambas variables, por lo que no ejecuta una
ingesta de producción silenciosamente sin notificación.
