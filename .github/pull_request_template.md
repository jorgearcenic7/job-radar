## Objetivo y alcance

<!-- Qué problema resuelve, qué cambia y qué queda deliberadamente fuera. -->

## Validación

<!-- Marca solo lo que aplique y añade resultados o motivos de omisión. -->

- [ ] Suite Python y `compileall`
- [ ] `git diff --check`
- [ ] Web: `npm ci`, audit de producción, lint y build
- [ ] Dependencias: audit y lockfile/manifests actualizados
- [ ] PostgreSQL: migración y tests con `TEST_DATABASE_URL`

## Revisiones condicionales

### Si cambia una fuente o conector

- [ ] Endpoint/slug y payload validados en vivo
- [ ] Mapping e identidad `(source, source_job_id)` cubiertos por tests sin red
- [ ] Impacto en cierres, reactivación y snapshot protection revisado

### Si cambia schema, operación o despliegue

- [ ] Migración incremental incluida y orden de despliegue documentado
- [ ] Variables, permisos y pasos manuales documentados
- [ ] Rollback o compatibilidad hacia atrás explicados

### Seguridad y documentación

- [ ] No contiene secretos, `.env`, datos personales ni volcados
- [ ] Entradas externas, URLs y SQL mantienen sus controles
- [ ] Documentación y ejemplos reflejan el comportamiento final

## Riesgos, breaking changes y pasos posteriores

<!-- Incluye riesgos conocidos, acciones tras merge o escribe "Ninguno". -->
