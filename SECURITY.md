# Política de seguridad

## Versiones con soporte

Job Radar mantiene únicamente el estado desplegable de `main`.

| Versión | Soporte de seguridad |
| --- | --- |
| `main` / producción | Sí |
| Commits o despliegues anteriores | No |

## Informar de una vulnerabilidad

No publiques vulnerabilidades, credenciales, pruebas de concepto ni datos
sensibles en una issue, discusión o pull request.

Usa el formulario privado **Security → Advisories → Report a vulnerability**:

<https://github.com/jorgearcenic7/job-radar/security/advisories/new>

Incluye, cuando sea posible:

- componente y commit afectados;
- pasos mínimos de reproducción;
- impacto observado o potencial;
- mitigación propuesta;
- prueba de concepto sin datos ni infraestructura de terceros.

El mantenedor intentará confirmar recepción en 7 días, clasificar el informe en
14 días y coordinar una corrección antes de divulgar detalles. Son objetivos,
no garantías contractuales.

## Alcance de seguridad

Son relevantes, entre otros:

- ejecución remota de código, inyección SQL, XSS y SSRF;
- exposición de secretos o datos personales;
- acceso no autorizado a PostgreSQL;
- manipulación de workflows o cadena de suministro;
- bypass de controles de URL, filtros, CSP o autorización externa;
- vulnerabilidades explotables en dependencias.

Un 403/404/5xx de un ATS, un slug obsoleto, una oferta incorrecta o un falso
positivo de matching son incidencias funcionales o de disponibilidad, no
vulnerabilidades salvo que tengan un impacto de seguridad independiente.

## Controles presentes en el código

La revisión del repositorio permite afirmar que:

- las consultas Python y web usan parámetros, no concatenación de filtros;
- la web limita filtros a 120 caracteres, página a 500 y aplica timeouts de
  conexión/query/statement de 10 segundos;
- los enlaces externos de web y correo solo se activan para URLs HTTPS;
- React renderiza texto externo sin `dangerouslySetInnerHTML`;
- `web/src/proxy.ts` genera una CSP con nonce por petición;
- `web/next.config.ts` añade HSTS, anti-framing, MIME sniffing, referrer,
  permissions policy y COOP;
- el pipeline escapa contenido del correo y admite idempotencia de Resend;
- los contenedores se ejecutan como usuario sin privilegios; Compose y el
  workflow añaden filesystem de solo lectura, `/tmp` temporal, capacidades
  eliminadas y `no-new-privileges`;
- `.env` y `web/.env.local` están ignorados y no se copian a la imagen;
- los workflows declaran permisos mínimos y fijan acciones por SHA;
- CI ejecuta `pip-audit`, audit npm de producción, Dependency Review y CodeQL.

Estos controles reducen riesgo, pero no constituyen una garantía de ausencia
de vulnerabilidades.

## Controles operativos no demostrables desde el repo

El código no puede verificar ni imponer:

- que `WEB_DATABASE_URL` pertenezca a un rol PostgreSQL con solo `SELECT`;
- backups, TLS, allowlists, rotación y límites configurados en Neon/PostgreSQL;
- variables, protección de despliegues, WAF o observabilidad de Vercel;
- activación de secret scanning, push protection, Dependabot alerts, private
  vulnerability reporting o rulesets de `main` en GitHub;
- verificación del dominio remitente, permisos y rotación de Resend.

Son recomendaciones operativas:

1. separar credenciales de ingesta y web;
2. conceder a la web únicamente `SELECT` sobre los objetos necesarios;
3. guardar secretos solo en el proveedor correspondiente;
4. proteger `main` con PR, revisión y checks obligatorios;
5. mantener activados los controles de seguridad disponibles en GitHub;
6. rotar credenciales ante cualquier exposición o cambio de responsables.

`web/src/lib/db.ts` exige `WEB_DATABASE_URL` cuando `VERCEL=1`, pero esa comprobación no
demuestra los privilegios reales del rol.

## Divulgación coordinada

Da tiempo razonable para investigar y desplegar una solución. El proyecto no
autoriza acceder, modificar o extraer datos ajenos, degradar servicios, eludir
controles de terceros ni probar contra infraestructura que no controles.

La [auditoría del 22 de septiembre de 2026](docs/security-audit-2026-09-22.md)
es histórica. Para el estado actual prevalecen este documento, el README y el
código de `main`.
