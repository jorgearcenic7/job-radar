# Contribuir a Job Radar

## Flujo de trabajo

1. Actualiza `main` y crea una rama con un objetivo único:

   ```bash
   git switch main
   git pull --ff-only origin main
   git switch -c tipo/descripcion-corta
   ```

2. Lee [AGENTS.md](AGENTS.md) y la documentación del componente. Para cambios
   en `web/`, lee también [web/AGENTS.md](web/AGENTS.md).
3. Haz el cambio mínimo, añade regresiones y revisa el diff completo.
4. Ejecuta los checks aplicables de esta guía.
5. Abre una pull request; no hagas push directo a `main`.

Mantén separadas las correcciones funcionales, migraciones, actualizaciones de
dependencias y refactors salvo que deban desplegarse juntas.

## Preparación

- Python 3.12 y dependencias de `requirements.txt`.
- Dependencias de desarrollo de `requirements-dev.txt` para `pip-audit`.
- Node.js 24 y `npm ci` bajo `web/` cuando aplique.
- PostgreSQL desechable para los tests de integración.

Parte de `.env.example` y `web/.env.example`. Nunca uses credenciales de
producción ni versiones reales de `.env` en commits, logs o fixtures.

## Checks Python

Estos comandos coinciden con el job **Python tests and dependency audit** de
`.github/workflows/tests.yml`:

```bash
python3 -m pip install -r requirements.txt -r requirements-dev.txt
python3 -m pip check
python3 -m pip_audit -r requirements.txt --strict
python3 -m compileall -q main.py job_radar tests
python3 -m unittest discover -s tests -v
```

CI levanta PostgreSQL 17 y define:

```bash
TEST_DATABASE_URL=postgresql://test:test@localhost:5432/job_radar_test
```

Localmente, los tests de lifecycle y `RunRepository` se omiten si esa variable
no existe. Usa siempre una base desechable: esos tests aplican SQL y eliminan
datos de prueba.

## Checks web

El job **Web lint, audit and build** ejecuta exactamente:

```bash
cd web
npm ci
npm audit --omit=dev --audit-level=high
npm audit --audit-level=high || true
npm run lint
npm run build
```

El audit de producción bloquea vulnerabilidades `high` o superiores. El audit
completo, que incluye herramientas de desarrollo, se reporta pero no bloquea
porque termina con `|| true`. No documentes ni cambies esa política sin cambiar
también el workflow y explicar el impacto.

En pull requests también se ejecuta Dependency Review con umbral `moderate` y
CodeQL para Python y JavaScript/TypeScript.

## Conectores y nuevas empresas

Antes de modificar código:

- identifica el ATS y valida el endpoint real desde terminal;
- confirma status, estructura, ofertas y campos consumidos;
- reutiliza un conector de `job_radar/connectors/ats.py` siempre que sea compatible;
- verifica que `(source, source_job_id)` sea una identidad estable;
- no inventes salario, experiencia ni ubicación;
- añade tests con red mockeada y revisa lifecycle/snapshot protection.

No conviertas un error HTTP o payload incompatible en `[]`: un falso snapshot
vacío oculta la causa y puede alterar la operación. La guía completa está en
[docs/CONNECTORS.md](docs/CONNECTORS.md).

## Schema y persistencia

- Añade un SQL numerado nuevo en `sql/`; no reescribas una migración aplicada.
- Usa consultas parametrizadas y transacciones explícitas mediante Psycopg.
- Mantén la clave `(source, source_job_id)` y los invariantes de cierre y
  reactivación.
- Actualiza tests de lifecycle, `RunRepository`, documentación de schema y
  pasos operativos.
- Describe en la PR el orden de despliegue y cualquier paso manual.

El repositorio no ejecuta migraciones automáticamente. Una migración necesaria
debe aplicarse antes de desplegar código que dependa de ella.

## Dependencias

Justifica cada dependencia nueva. Python fija versiones directas en
`requirements*.txt`; npm usa `web/package-lock.json`. Actualiza manifest y
lockfile juntos, ejecuta los audits correspondientes y no edites el lockfile a
mano.

## Seguridad

- No incluyas secretos, tokens, datos personales, volcados ni URLs privadas.
- Mantén entradas acotadas, enlaces HTTPS y SQL parametrizado.
- Distingue controles implementados en código de configuraciones externas de
  GitHub, PostgreSQL/Neon o Vercel.
- Informa vulnerabilidades por el canal privado descrito en
  [SECURITY.md](SECURITY.md), no mediante issue o PR pública.

## Pull request

La descripción debe explicar objetivo, alcance, riesgos y validación. Marca
solo los bloques aplicables de la plantilla. Señala de forma explícita:

- breaking changes o cambios de contrato;
- migraciones y orden de despliegue;
- endpoints externos validados;
- dependencias añadidas/actualizadas;
- pasos manuales posteriores.

Antes de entregar:

```bash
git diff --check
git status --short
```
