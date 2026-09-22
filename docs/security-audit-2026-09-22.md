# Auditoría de seguridad y calidad — 22 de septiembre de 2026

## Resumen ejecutivo

La revisión cubre el pipeline Python, la aplicación Next.js, PostgreSQL,
Docker, dependencias y GitHub Actions. No se ha identificado una vulnerabilidad
crítica explotable directamente en el código revisado. Sí se detectaron varias
carencias de defensa en profundidad y de automatización que se corrigen en este
cambio.

La conclusión se basa en revisión manual, búsqueda de patrones sensibles,
historial de Git para ficheros de entorno, pruebas unitarias disponibles y
consulta de avisos públicos. CodeQL, `pip-audit` y `npm audit` quedan integrados
en CI para repetir el análisis con bases de vulnerabilidades actualizadas.

## Hallazgos y tratamiento

| Severidad | Hallazgo | Estado |
| --- | --- | --- |
| Alta (potencial) | `curl_cffi` no garantizaba una versión parcheada; entornos existentes con una versión anterior a 0.15.0 pueden estar afectados por una SSRF por redirecciones (CVE-2026-33752). | Corregido: fijado a 0.16.3. |
| Media | Code scanning, Dependabot y revisión de dependencias no estaban configurados en el repositorio. | Corregido en ficheros; quedan activaciones de interfaz indicadas abajo. |
| Media | Los filtros y la página aceptaban tamaños arbitrarios, permitiendo consultas costosas o errores por offsets extremos. | Corregido: filtros y paginación acotados; timeout SQL de 10 segundos. |
| Media | Las URLs obtenidas de terceros se renderizaban sin validar el esquema. | Corregido: solo se enlazan URLs HTTPS válidas. |
| Media | La web no enviaba una CSP ni cabeceras explícitas contra framing, MIME sniffing y permisos innecesarios. | Corregido: CSP con nonce por petición y cabeceras defensivas. |
| Baja | El contenedor del pipeline se ejecutaba como `root` y sin restricciones adicionales. | Corregido: usuario sin privilegios UID 10001, filesystem de solo lectura, capacidades eliminadas y `no-new-privileges`. |
| Baja | Dos dependencias Python directas no estaban fijadas, permitiendo cambios inesperados entre builds. | Corregido: versiones directas exactas y actualizaciones por Dependabot. |
| Baja | Los workflows usaban referencias móviles antiguas y conservaban credenciales de checkout sin necesitarlas. | Corregido: acciones fijadas por SHA y `persist-credentials: false`. |
| Informativa | Existe un identificador público de board de Comeet en `main.py`. Puede parecer un token, pero forma parte de una API pública de ofertas. | Aceptado; no concede acceso administrativo según el uso revisado. |
| Informativa | No puede verificarse desde el repositorio si la credencial PostgreSQL de Vercel tiene permisos de escritura. | Pendiente operativo: usar un rol exclusivo con `SELECT` sobre `jobs`. |

## Controles positivos observados

- `.env` y `web/.env.local` están ignorados y no aparecen en el historial de
  Git revisado.
- Las consultas SQL usan parámetros; no se encontró concatenación de entrada
  de usuario en sentencias.
- React escapa los campos procedentes de ofertas y no se usa
  `dangerouslySetInnerHTML`.
- Los enlaces externos ya incluían `noopener noreferrer`.
- El workflow de ingesta aplica permisos mínimos (`contents: read`) y no
  imprime `DATABASE_URL`.
- Los conectores construyen las peticiones sobre hosts conocidos; no se
  encontró un destino HTTP controlable directamente por un usuario web.
- Next.js 16.3.4 está por encima de las versiones corregidas 16.3.3 para los
  avisos críticos publicados en agosto de 2026 sobre optimización AVIF y hosts
  Windows.

## Riesgos residuales

1. Los portales de empleo son entradas no confiables. Aunque React escapa el
   texto y las URLs se validan, respuestas excepcionalmente grandes aún pueden
   consumir memoria durante la ingesta. Conviene añadir un límite de bytes si
   algún proveedor deja de ser estable.
2. La búsqueda `strpos(lower(title), ...)` no puede aprovechar un índice B-tree.
   Los límites y timeouts reducen el riesgo, pero para un volumen grande debería
   usarse un índice trigram o full-text y rate limiting en el perímetro.
3. Las imágenes base de Docker no están fijadas por digest. Dependabot vigila
   actualizaciones, pero un digest aportaría reproducibilidad e inmutabilidad
   mayores.
4. Python aún resuelve dependencias transitivas sin un lockfile con hashes. Las
   versiones directas están fijadas y se auditan en CI, pero un `pylock.toml` o
   fichero generado con hashes reforzaría la cadena de suministro.
5. La auditoría local completa de npm no pudo ejecutarse en este entorno porque
   el binario Node disponible pertenece a una instalación WSL incompatible. El
   workflow de CI añadido ejecuta `npm audit`, lint y build con Node 24.
6. Las pruebas PostgreSQL de ciclo de vida requieren una base de pruebas. En la
   revisión local se omitieron; el workflow existente sí levanta el servicio.
7. La separación de privilegios de PostgreSQL depende de Neon/Vercel y no puede
   imponerse solo desde este repositorio. La web debe usar una credencial de
   solo lectura distinta de la credencial de ingesta.
8. Docker Desktop no tiene habilitada la integración con esta distribución WSL,
   por lo que el build endurecido no pudo ejecutarse localmente. El workflow de
   ingesta lo validará tras subir los cambios.

## Acciones manuales en GitHub

Los ficheros del repositorio no pueden activar todos los controles de cuenta.
Tras fusionar y subir este cambio:

1. En **Settings → Security and quality**, activa **Dependabot alerts** y
   **Dependabot security updates**.
2. Activa **Private vulnerability reporting** para que funcione el canal de
   `SECURITY.md`.
3. Confirma que el workflow **CodeQL** termina correctamente. No actives a la
   vez el *default setup* de CodeQL: este repositorio usa *advanced setup*.
4. Mantén activados **Secret scanning** y, si está disponible, **Push
   protection**.
5. Crea una ruleset para `main`: pull request obligatoria, una aprobación del
   `CODEOWNER`, conversaciones resueltas y checks obligatorios `Python tests and
   dependency audit`, `Web lint, audit and build`, `Dependency review` y los dos
   análisis de CodeQL.
6. Revisa cualquier alerta inicial antes de permitir merges automáticos de
   Dependabot.
7. En Neon crea un rol de solo lectura para la aplicación web y configura con él
   `DATABASE_URL` en Vercel; reserva la credencial de escritura para el secret
   de ingesta de GitHub Actions.

## Verificación realizada

- 16 tests Python: 14 correctos y 2 omitidos por falta de PostgreSQL local.
- Consulta por lotes a OSV: 458 versiones comprobadas (455 paquetes npm del
  lockfile y 3 dependencias Python directas), sin vulnerabilidades conocidas
  devueltas el 22 de septiembre de 2026.
- Búsqueda de secretos y sinks comunes: sin credenciales privadas confirmadas,
  sin `eval`, `shell=True`, `pickle`, `dangerouslySetInnerHTML` ni SQL dinámico
  con entrada web.
- Verificación de que los ficheros de entorno no están versionados ni aparecen
  en su historial de Git.
- Revisión manual de permisos y manejo de secretos en GitHub Actions.

Las comprobaciones automatizadas nuevas deben considerarse parte continua de
la auditoría, no una certificación puntual de ausencia de vulnerabilidades.

## Referencias

- [GitHub: configuración avanzada de CodeQL](https://docs.github.com/en/code-security/how-tos/find-and-fix-code-vulnerabilities/configure-code-scanning/configuring-advanced-setup-for-code-scanning)
- [GitHub: actualizaciones de versión con Dependabot](https://docs.github.com/en/code-security/how-tos/secure-your-supply-chain/secure-your-dependencies/configure-version-updates)
- [GHSA-qw2m-4pqf-rmpp: SSRF en curl_cffi](https://github.com/advisories/GHSA-qw2m-4pqf-rmpp)
- [GHSA-2xp9-vwfh-vxw4: RCE en optimización AVIF de Next.js](https://github.com/advisories/GHSA-2xp9-vwfh-vxw4)
- [GHSA-p293-qw3h-jr36: RCE de Next.js en hosts Windows](https://github.com/advisories/GHSA-p293-qw3h-jr36)
