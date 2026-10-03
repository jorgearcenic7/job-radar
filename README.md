# Job Radar

Job Radar aggregates, normalizes and evaluates Data Engineering opportunities
directly from company career sites through an automated ingestion pipeline.
No es solo un scraper: integra múltiples ATS y portales propios, aplica reglas
transparentes de matching, protege el ciclo de vida de las ofertas ante
snapshots anómalos y publica los resultados en una aplicación web.

### [Abrir la aplicación →](https://job-radar-snowy.vercel.app)

![Job Radar dashboard](docs/assets/job-radar-dashboard.png)

[![Automated Tests](https://github.com/jorgearcenic7/job-radar/actions/workflows/tests.yml/badge.svg)](https://github.com/jorgearcenic7/job-radar/actions/workflows/tests.yml)
[![CodeQL](https://github.com/jorgearcenic7/job-radar/actions/workflows/codeql.yml/badge.svg)](https://github.com/jorgearcenic7/job-radar/actions/workflows/codeql.yml)
[![License: Apache 2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)

[Demo](https://job-radar-snowy.vercel.app) ·
[Arquitectura](#arquitectura) ·
[Matching](#criterios-de-matching) ·
[Entorno local](#puesta-en-marcha-local) ·
[Tests](#pruebas-y-calidad)

## En un vistazo

- **57 integraciones empresa/fuente configuradas** mediante Greenhouse, Ashby,
  Workday y otros ATS, además de portales propios.
- **Pipeline Python y persistencia PostgreSQL** con upserts idempotentes para
  normalización, matching y seguimiento de ofertas abiertas, cerradas y
  reactivadas.
- **Dashboard Next.js** con búsqueda, filtros y acceso a las oportunidades
  activas.
- **Operación automatizada** en días alternos a las 06:00 (Europe/Madrid) con
  GitHub Actions, observabilidad persistente por ejecución y fuente, y
  protección frente a snapshots vacíos o anómalos.
- **Controles de calidad automatizados** con tests, auditorías de dependencias,
  Dependency Review y CodeQL.

## Funcionalidades

- Matching basado en puesto, seniority, experiencia requerida y señales
  técnicas.
- Etiquetas **Buena coincidencia** y **Stretch** para las ofertas seleccionadas.
- Extracción de salario y experiencia cuando la empresa los publica.
- Normalización de países sin alterar la ubicación original.
- Notificación por correo con todas las coincidencias activas al terminar cada
  ejecución.
- Web con búsqueda, filtros, paginación y España seleccionada por defecto.

## Arquitectura

```mermaid
flowchart LR
    A[ATS y portales de empleo] --> B[Pipeline Python]
    B --> C[Normalización y matching]
    C --> D[(PostgreSQL / Neon)]
    D --> E[Next.js en Vercel]
    F[GitHub Actions] -->|Ejecución en días alternos| B
```

El pipeline conserva la descripción y la ubicación publicadas, calcula los
campos derivados y actualiza cada oferta usando `(source, source_job_id)` como
clave. La web solo muestra registros activos y consulta la base de datos desde
el servidor. `main.py` es únicamente el punto de entrada; los conectores, el
dominio, el matching, la orquestación, las notificaciones y la persistencia se
mantienen en módulos separados bajo `job_radar/`.

## Empresas y fuentes

| Fuente | Empresas |
| --- | --- |
| Greenhouse | Typeform, N26, Stripe, Adyen, Block (incluye Afterpay), Chime, Nubank, Robinhood, SoFi, Coinbase, Datadog, Clarity AI, Fever, Cabify, Aircall, Auctane, Celonis, Taxbit, Ebury, Lynx, Monzo |
| Ashby | Pleo, Plaid, Qonto, Mollie, Capchase, Invopop, Airwallex, Checkout.com, Rain, Lovable |
| Workday | Mastercard, BBVA, Santander, Amadeus, AVEVA |
| SuccessFactors | SAP, Hexagon |
| SmartRecruiters | IFS, Wise, Grab / Grab Financial Group |
| Lever | Paytm |
| Deel Jobs | Deel, Klarna |
| BambooHR | Flutterwave |
| Eightfold | PayPal |
| Teamtailor | Spendesk, Seedtag, Lingokids |
| Comeet | ThetaRay |
| iCIMS | Mambu |
| Sage People | Sage |
| Portales propios | Ant Group / Ant International, CaixaBank Tech, Dassault Systèmes, Revolut, Visma |

La disponibilidad cambia continuamente, por lo que una empresa compatible
puede no tener ofertas activas en un momento determinado.

## Criterios de matching

El perfil objetivo tiene aproximadamente un año de experiencia y está
orientado principalmente a:

- Data Engineer, Analytics Engineer y BI Engineer.
- Data Platform, Data Infrastructure y Data Warehouse Engineer.
- ETL/ELT Engineer y DataOps Engineer.

También se aceptan como *Stretch* puestos técnicos adyacentes —por ejemplo,
Data Analyst, Data Scientist, ML/MLOps, Platform Engineer o Software Engineer—
cuando la descripción contiene suficientes responsabilidades de datos.

Se excluyen prácticas y puestos Graduate, Trainee, Senior, Staff, Principal,
Lead, Manager, Director o Architect. También se descartan niveles IV o
superiores y ofertas que exigen al menos cuatro años de experiencia. Se
admiten puestos Junior, Associate, niveles I-III y requisitos de hasta tres
años. Para marcar una oferta como coincidencia, su ubicación debe estar en
cualquier punto de España o ser remoto sin restricción de país o abierto a
España/Europa.

Entre las señales técnicas se encuentran pipelines, ETL/ELT, modelado de
datos, data warehouses, lakes y lakehouses, procesamiento batch o streaming,
SQL, Python, Spark, Databricks, Airflow, dbt, Kafka, Snowflake, BigQuery,
Redshift, Microsoft Fabric y plataformas cloud.

Las reglas están implementadas en
[`classify`](job_radar/matching/rules.py) y nunca inventan un salario o una
experiencia que la fuente no haya publicado.

## Puesta en marcha local

### Requisitos

- Python 3.12.
- PostgreSQL 17.
- Node.js 24 y npm.
- Docker y Docker Compose, opcionales para ejecutar la ingesta en contenedores.

### 1. Configurar PostgreSQL

Crea una base de datos local y aplica el esquema:

```bash
createdb job_radar
psql job_radar < sql/001_create_jobs.sql
psql job_radar < sql/002_add_job_lifecycle.sql
psql job_radar < sql/003_add_ingestion_observability.sql
psql job_radar < sql/004_add_snapshot_health.sql
```

Copia las variables de ejemplo y sustituye la contraseña por tus credenciales
locales:

```bash
cp .env.example .env
cp web/.env.example web/.env.local
```

`DATABASE_URL` se utiliza en la ingesta. La web usa `WEB_DATABASE_URL` y, fuera
de Vercel, admite `DATABASE_URL` como alternativa. Ninguna de las dos debe
llevar el prefijo `NEXT_PUBLIC_`.

### 2. Ejecutar la ingesta

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
set -a
source .env
set +a
python main.py
```

La ejecución consulta servicios externos y guarda el snapshot actual de cada
empresa. El log muestra el tiempo total de ingesta de cada fuente, incluida la
consulta y la escritura en base de datos, también cuando una fuente falla. Para
ejecutar únicamente la ingesta y PostgreSQL con Docker:

```bash
docker compose up -d db
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/001_create_jobs.sql
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/002_add_job_lifecycle.sql
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/003_add_ingestion_observability.sql
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < sql/004_add_snapshot_health.sql
docker compose run --rm pipeline
```

Cada ejecución crea una fila en `ingestion_runs` y una fila por fuente en
`source_runs`. Se registran estado, duración, ofertas vistas, nuevas y cerradas,
coincidencias y el tipo/mensaje de error cuando corresponde. Los logs incluyen
el `run_id` y las mismas métricas para facilitar el diagnóstico inmediato.
Cada fuente registra además `snapshot_status` y `closure_suppressed`; los
snapshots vacíos o sospechosos actualizan las ofertas recibidas pero no cierran
las ausentes.

### 3. Ejecutar la web

Con PostgreSQL accesible mediante la URL configurada en `web/.env.local`:

```bash
cd web
npm ci
npm run dev
```

La aplicación estará disponible en <http://localhost:3000>.

## Notificaciones por correo

El pipeline envía mediante [Resend](https://resend.com) un correo HTML y texto
plano después de cada ejecución. Incluye todas las coincidencias activas, su
clasificación, empresa, ubicación, salario, experiencia y enlace original. Si
alguna fuente falla, el asunto y el resumen indican que los resultados son
parciales.

Para activar el envío automático, crea estos secretos en **Settings → Secrets
and variables → Actions** del repositorio:

| Secreto | Valor |
| --- | --- |
| `RESEND_API_KEY` | API key creada en Resend (`re_...`) |
| `NOTIFICATION_EMAIL` | Dirección que recibirá el listado |
| `NOTIFICATION_FROM` | Remitente de un dominio verificado, opcional |

Si no se configura `NOTIFICATION_FROM`, se utiliza
`Job Radar <onboarding@resend.dev>`. Para las primeras pruebas puede usarse
este remitente; para un envío estable se recomienda verificar un dominio en
Resend. El workflow exige los dos primeros secretos y usa una clave de
idempotencia por ejecución para evitar correos duplicados durante reintentos.

En local, las mismas variables se pueden definir en `.env`. Si
`RESEND_API_KEY` está vacía, el envío se omite.

## Pruebas y calidad

```bash
python -m pip install -r requirements.txt -r requirements-dev.txt
python -m compileall -q main.py job_radar tests
python -m unittest discover -s tests -v

cd web
npm ci
npm run lint
npm run build
```

Los tests unitarios cubren extracción, matching, generación segura del correo
y conectores representativos de Greenhouse, Ashby, Workday y CaixaBank Tech.
Los tests de ciclo de vida y del repositorio de ejecuciones requieren una base
PostgreSQL desechable:

```bash
TEST_DATABASE_URL=postgresql://user:password@localhost:5432/job_radar_test \
  python -m unittest tests.test_lifecycle -v
```

CI también ejecuta auditorías de dependencias de Python y npm, CodeQL y
Dependency Review. Consulta [`CONTRIBUTING.md`](CONTRIBUTING.md) antes de abrir
una pull request.

## Stack

- Python 3.12, Psycopg 3, geonamescache y curl_cffi.
- PostgreSQL 17 en local y Neon en producción.
- Next.js 16, React 19, TypeScript y Tailwind CSS 4.
- Docker, GitHub Actions y Vercel.

## Seguridad

- Los secretos se proporcionan mediante variables de entorno o GitHub Secrets.
- Producción separa la conexión de ingesta de la conexión web de solo lectura.
- Las consultas usan parámetros y la web limita los filtros recibidos.
- Los enlaces externos se restringen a HTTPS y la aplicación aplica una CSP
  con nonce.
- Los contenedores se ejecutan sin privilegios, con capacidades eliminadas y
  filesystem de solo lectura cuando corresponde.
- Los workflows tienen permisos mínimos y sus acciones están fijadas por SHA.

No incluyas archivos `.env`, credenciales, volcados de base de datos ni datos
personales en el repositorio. Para informar de una vulnerabilidad, sigue
[`SECURITY.md`](SECURITY.md). La revisión más reciente está en
[`docs/security-audit-2026-09-22.md`](docs/security-audit-2026-09-22.md).

## Limitaciones y roadmap

- [x] Pipeline de ingesta dockerizado.
- [x] Persistencia PostgreSQL con upserts idempotentes.
- [x] Web desplegada en Vercel y base de datos Neon.
- [x] Ejecución automatizada en días alternos.
- [x] Matching, extracción de salario/experiencia y normalización de países.
- [x] Detección segura de cierres y reactivaciones.
- [x] Suite inicial de tests de conectores y ciclo de vida.
- [x] Notificaciones por correo con el listado de coincidencias.
- [ ] Ampliar la cobertura automatizada a todos los conectores personalizados.
- [ ] Incorporar más empresas de producto, software y FinTech.

El matching sigue siendo heurístico y los ATS externos pueden cambiar sus APIs
o su HTML sin previo aviso.

## Licencia

Distribuido bajo la [Apache License 2.0](LICENSE). Consulta también
[NOTICE](NOTICE) para la atribución del proyecto.
