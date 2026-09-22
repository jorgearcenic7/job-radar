# Política de seguridad

## Versiones con soporte

Job Radar mantiene únicamente la versión desplegada desde la rama `main`.

| Versión | Soporte de seguridad |
| --- | --- |
| `main` / producción | Sí |
| Versiones o commits anteriores | No |

## Cómo informar de una vulnerabilidad

No publiques vulnerabilidades, credenciales, pruebas de concepto ni datos
sensibles en una issue pública.

Usa el formulario privado **Security → Advisories → Report a
vulnerability** del repositorio:

<https://github.com/jorgearcenic7/job-radar/security/advisories/new>

Incluye, cuando sea posible:

- componente y versión afectados;
- pasos mínimos para reproducir el problema;
- impacto observado o potencial;
- una propuesta de mitigación;
- cualquier prueba de concepto, sin datos de terceros.

El mantenedor intentará confirmar la recepción en un máximo de 7 días,
clasificar el informe en 14 días y coordinar una corrección antes de hacer
públicos los detalles. Estos plazos son objetivos, no garantías.

## Alcance

Son especialmente relevantes la ejecución remota de código, inyección SQL,
XSS, SSRF, exposición de secretos, acceso no autorizado a PostgreSQL,
manipulación de los workflows y vulnerabilidades explotables en dependencias.

Los errores de disponibilidad de portales de empleo de terceros, ofertas
incorrectas y fallos de matching sin impacto de seguridad deben comunicarse
mediante una issue normal.

## Divulgación coordinada

Da tiempo razonable para investigar y desplegar una solución. El proyecto no
autoriza acceder, modificar ni extraer datos ajenos, degradar el servicio ni
realizar pruebas sobre infraestructura de terceros.
