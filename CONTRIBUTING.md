# Contribuir a Job Radar

## Preparación

1. Crea tus variables locales a partir de `.env.example` y
   `web/.env.example`. No uses credenciales de producción.
2. Instala Python 3.12 y las dependencias de `requirements.txt`.
3. En `web/`, usa Node.js 24 y ejecuta `npm ci` para respetar el lockfile.

## Comprobaciones antes de abrir una pull request

```bash
python -m compileall -q main.py tests
python -m unittest discover -s tests -v
python -m pip_audit -r requirements.txt --strict

cd web
npm ci
npm audit --audit-level=high
npm run lint
npm run build
```

Las pruebas de ciclo de vida necesitan `TEST_DATABASE_URL` apuntando a una
base PostgreSQL de pruebas desechable.

## Pull requests

- Limita cada cambio a un objetivo claro.
- Añade o actualiza pruebas cuando cambie el comportamiento.
- No incluyas `.env`, volcados de base de datos, tokens ni datos personales.
- Usa consultas SQL parametrizadas.
- Justifica dependencias nuevas y conserva sus lockfiles.
- Para fallos de seguridad, sigue `SECURITY.md` y no abras una PR pública
  antes de coordinar la divulgación.
