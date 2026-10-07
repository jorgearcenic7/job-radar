# Job Radar Web

Aplicación server-side que publica las ofertas activas almacenadas por el
pipeline Python. No ingiere fuentes ni ejecuta matching: consume el resultado
persistido en la tabla `jobs`.

## Stack

- Next.js 16.3.8 con App Router y Turbopack por defecto.
- React y React DOM 19.3.0.
- TypeScript 5.9.3 en modo estricto.
- Tailwind CSS 4.3.3 mediante PostCSS.
- `pg` 8.23.0 para PostgreSQL.
- Node.js 24 en CI y `compose.web.yaml`.

Las versiones exactas resueltas están en `package-lock.json`.

## Estructura

| Ruta | Responsabilidad |
| --- | --- |
| `src/app/page.tsx` | Server Component, consultas, filtros, paginación y UI. |
| `src/app/layout.tsx` | Metadata, idioma y fuentes Geist. |
| `src/app/globals.css` | Estilos globales y componentes visuales. |
| `src/lib/db.ts` | Pool PostgreSQL server-only y selección de credenciales. |
| `src/proxy.ts` | CSP con nonce por petición. |
| `next.config.ts` | Cabeceras HTTP defensivas. |

Para cambios de Next.js, lee `AGENTS.md` antes de editar: la versión instalada
incluye documentación local bajo `node_modules/next/dist/docs/`.

## Acceso a datos

La página usa `dynamic = "force-dynamic"` y consulta PostgreSQL directamente
desde el servidor. No hay API route ni acceso a base de datos desde el
navegador.

Las consultas:

- muestran solo `jobs.active = TRUE`;
- buscan por título y filtran por empresa, país y `selected`;
- seleccionan España por defecto;
- ordenan primero las coincidencias;
- paginan 20 filas, limitan filtros a 120 caracteres y página a 500;
- usan placeholders de `pg` para todos los valores externos.

El pool admite hasta cinco conexiones y configura timeouts de conexión, query
y statement. El código solo emite `SELECT`, pero los permisos read-only del rol
se deben configurar en PostgreSQL: el repo no puede imponerlos.

## Variables de entorno

Copia el ejemplo:

```bash
cp .env.example .env.local
```

| Variable | Uso |
| --- | --- |
| `WEB_DATABASE_URL` | Conexión server-side preferida y obligatoria en Vercel. |
| `DATABASE_URL` | Fallback local cuando `VERCEL` no es `1`. |

Nunca uses `NEXT_PUBLIC_` para una credencial. En Vercel, `web/src/lib/db.ts` rechaza el
fallback a `DATABASE_URL` para reducir el riesgo de reutilizar la credencial de
escritura creada por una integración.

## Desarrollo local

La base debe tener aplicados los SQL de `../sql/` y datos en `jobs`.

```bash
npm ci
npm run dev
```

Abre <http://localhost:3000>.

También puedes usar `compose.web.yaml` junto al servicio `db` de
`compose.yaml`; monta este directorio y expone el dev server en
`127.0.0.1:3000`.

```bash
docker compose -f ../compose.yaml -f ../compose.web.yaml up db web
```

## Validación

Comandos equivalentes al job web de CI:

```bash
npm ci
npm audit --omit=dev --audit-level=high
npm audit --audit-level=high || true
npm run lint
npm run build
```

El audit de producción bloquea; el audit completo es informativo en CI. El
build usa `next/font/google`, por lo que el entorno de compilación necesita
acceso a Google Fonts salvo que las fuentes ya estén disponibles en caché.

## Seguridad HTTP

`src/proxy.ts` genera un nonce por petición, lo pasa como cabecera interna y
envía una CSP para scripts y estilos. En desarrollo permite `unsafe-eval` para
las herramientas de Next.js. `web/next.config.ts` añade:

- `Strict-Transport-Security`;
- `X-Frame-Options: DENY`;
- `X-Content-Type-Options: nosniff`;
- `Referrer-Policy`;
- `Permissions-Policy`;
- `Cross-Origin-Opener-Policy`.

Las URLs de ofertas se enlazan únicamente si son HTTPS y se abren con
`noopener noreferrer`.

## Despliegue en Vercel

El proyecto es desplegable como aplicación Next.js con root directory `web`.
El repositorio no contiene `vercel.json` ni aprovisiona el proyecto.

Configura externamente:

1. `WEB_DATABASE_URL` como secreto server-side;
2. un rol PostgreSQL separado con solo `SELECT`;
3. la versión de Node compatible con CI (24);
4. checks de build antes de promoción.

TLS, protección de despliegues, WAF, límites y logs son configuración de
Vercel y no se pueden verificar desde este directorio.

## Relación con el pipeline

El pipeline raíz escribe y actualiza `jobs`, incluida la selección, países y
lifecycle. La web no dispara ingestas ni modifica registros. Consulta
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md) para el flujo completo y
[`../docs/OPERATIONS.md`](../docs/OPERATIONS.md) para operación.
