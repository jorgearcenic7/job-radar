# Instrucciones para agentes

## Propósito

Job Radar ingiere ofertas de career sites, las normaliza, aplica reglas
explícitas para un perfil de Data Engineering y conserva su lifecycle en
PostgreSQL. Una web Next.js consulta las ofertas activas y el pipeline puede
enviar coincidencias por Resend.

## Mapa del repositorio

| Responsabilidad | Fuente de verdad |
| --- | --- |
| Punto de entrada | `main.py` |
| Conectores y utilidades HTTP | `job_radar/connectors/` |
| Empresas y orden de ejecución | `job_radar/connectors/registry.py` |
| Modelo `Job` | `job_radar/domain/models.py` |
| Matching, salario, experiencia y países | `job_radar/matching/rules.py` |
| Persistencia y lifecycle | `job_radar/storage/` |
| Snapshot protection | `job_radar/orchestration/snapshots.py` |
| Ejecución, errores y estados | `job_radar/orchestration/runner.py` |
| Salud operativa de fuentes | `job_radar/observability/health.py` |
| Correo | `job_radar/notifications/` |
| Schema y migraciones | `sql/` |
| Aplicación web | `web/` |
| CI y ejecución programada | `.github/workflows/` |

Para cualquier cambio bajo `web/`, lee además `web/AGENTS.md`. Sus reglas de
Next.js son específicas y no se duplican aquí.

## Reglas de cambio

- Mantén `main.py` como entrada mínima; la lógica pertenece al módulo dueño.
- Reutiliza un conector ATS antes de crear lógica personalizada. Valida el
  endpoint real y su payload antes de registrar una empresa.
- Todo conector devuelve `list[Job]` y preserva `(source, source_job_id)` como
  identidad estable. No inventes salario, experiencia, ubicación ni URL.
- No rompas cierre/reactivación: un job visto queda activo; uno ausente solo se
  cierra cuando snapshot protection lo permite.
- No conviertas un snapshot vacío o sospechoso en cierres masivos.
- Mantén SQL parametrizado. Todo cambio de schema requiere una migración nueva,
  incremental y posterior al último número en `sql/`; nunca edites una
  migración histórica aplicada, porque su checksum es inmutable.
- No añadas secretos, `.env`, datos personales ni credenciales. La web debe
  seguir usando variables server-side sin prefijo `NEXT_PUBLIC_`.
- Conserva la semántica de fuentes `required`, opcionales y `catch_all`; revisa
  `job_radar/orchestration/runner.py` y sus tests antes de alterarla.
- La salud histórica se deriva de `source_runs` en
  `job_radar/observability/health.py`. No confundas un fallo de ejecución con
  `snapshot_status`: son señales distintas. Mantén explícitos y cubiertos por
  tests los umbrales y el SLO de fuentes `required`.
- `MATCH_RULES_VERSION` vive únicamente en `job_radar/matching/rules.py`; todo
  cambio que altere decisiones de matching debe incrementarla. Los reason
  codes son un contrato machine-readable estable: no los reutilices con otro
  significado. `match_reason` es texto humano y puede evolucionar.
- El flujo es `Job` normalizado → `prepare_job()` → `PreparedJob` → storage.
  La preparación clasifica y deriva países; `job_radar/storage/` nunca debe
  importar ni ejecutar lógica de `job_radar/matching/`.
- Evita dependencias nuevas si la biblioteca estándar o una dependencia actual
  resuelve el problema. Actualiza manifests y lockfiles juntos cuando aplique.
- Una decisión arquitectónica significativa puede requerir un ADR en
  `docs/adr/`. Un ADR aceptado no se reescribe para reflejar una decisión
  nueva: se crea otro que lo sustituya.
- Trabaja en una rama y mediante PR. No hagas push directo a `main`.

## Añadir una fuente

1. Identifica el ATS desde el career site.
2. Comprueba el endpoint con terminal: status, estructura, ofertas y campos que
   consume el fetcher.
3. Reutiliza `fetch_greenhouse`, `fetch_ashby` u otro conector existente.
4. Añade empresa/slug a `job_radar/connectors/registry.py`; crea un custom solo si ningún ATS
   existente sirve y el cambio lo pide explícitamente.
5. Valida el mapping a `Job`, incluida una identidad estable y URL HTTPS.
6. Añade tests sin llamadas reales a Internet.
7. Regenera el catálogo con `python3 scripts/update_source_catalog.py`.
8. Ejecuta toda la suite y revisa el impacto en lifecycle y snapshots.

La guía detallada está en `docs/CONNECTORS.md`.

## Convenciones

- Runtime Python: 3.12. Tests: `unittest` y `unittest.mock`.
- Conserva el estilo de imports existente: estándar, terceros y proyecto; usa
  imports absolutos entre paquetes `job_radar` y relativos dentro del paquete
  de conectores cuando ya sea la convención local.
- Usa `logging`, no `print`, en producción. Los logs operativos siguen formato
  `evento clave=valor` e incluyen identificadores y métricas útiles.
- Propaga fallos inesperados. Los fallos recuperables de fuente se registran en
  `source_runs`; no añadas `except Exception` silenciosos.
- Prefiere cambios pequeños y tests de regresión. Haz explícitas las heurísticas
  y sus límites.
- No hay formatter Python configurado: respeta PEP 8 y el estilo del archivo.

## Validación obligatoria

Para cualquier cambio Python o documental con referencias técnicas:

```bash
python3 scripts/update_source_catalog.py --check
python3 -m compileall -q main.py job_radar tests scripts
python3 -m unittest discover -s tests -v
git diff --check
```

Si cambian dependencias Python:

```bash
python3 -m pip check
python3 -m pip_audit -r requirements.lock --strict
```

Si cambia `web/` o su documentación técnica:

```bash
cd web
npm ci
npm audit --omit=dev --audit-level=high
npm run lint
npm run build
```

Las pruebas de PostgreSQL requieren `TEST_DATABASE_URL`; CI proporciona una
base desechable. Antes de entregar, revisa el diff completo y confirma que no
hay archivos generados ni cambios fuera de alcance.

## Principios

Prioriza robustez, mantenibilidad, observabilidad, comportamiento explícito y
regresiones cubiertas. La disponibilidad de una fuente externa no justifica
degradar lifecycle, seguridad o trazabilidad del resto del pipeline.
