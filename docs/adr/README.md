# Architecture Decision Records

Un ADR registra una decisión arquitectónica significativa de Job Radar: el
contexto en que se tomó, la decisión y sus consecuencias. Describe por qué el
sistema es como es; el comportamiento detallado sigue en
[ARCHITECTURE.md](../ARCHITECTURE.md) y en el código.

Un ADR `Accepted` no se reescribe para reflejar una decisión nueva. Si la
decisión cambia, se crea otro ADR que la sustituya y el anterior pasa a
`Superseded by ADR XXXX`. Solo se admiten correcciones menores, como enlaces o
erratas.

| ADR | Decisión | Status |
| --- | --- | --- |
| [0001](0001-versioned-sql-migrations.md) | Migraciones SQL versionadas sin Alembic/SQLAlchemy | Accepted |
| [0002](0002-versioned-matching-and-preparation.md) | Matching versionado y separación entre clasificación y persistencia | Accepted |

## Añadir un ADR

1. Crea `NNNN-titulo-en-kebab-case.md` con el siguiente número libre; el
   próximo es `0003-...md`.
2. Usa las secciones `Status`, `Context`, `Decision` y `Consequences`.
3. Describe el estado real del repositorio y separa consecuencias positivas y
   trade-offs.
4. Añádelo a la tabla de este índice en el mismo PR que la decisión.
