import { db } from "@/lib/db";

export const dynamic = "force-dynamic";

const MAX_FILTER_LENGTH = 120;
const MAX_PAGE = 500;

function boundedParam(value: string | string[] | undefined, fallback = "") {
  return typeof value === "string"
    ? value.trim().slice(0, MAX_FILTER_LENGTH)
    : fallback;
}

function safeExternalUrl(value: string) {
  try {
    const url = new URL(value);
    return url.protocol === "https:" ? url.toString() : null;
  } catch {
    return null;
  }
}

function JobLink({ url }: { url: string }) {
  const safeUrl = safeExternalUrl(url);

  if (!safeUrl) {
    return (
      <span className="job-link cursor-not-allowed opacity-50">
        Enlace no disponible
      </span>
    );
  }

  return (
    <a
      href={safeUrl}
      target="_blank"
      rel="noopener noreferrer"
      className="job-link"
    >
      Ver oferta
      <span aria-hidden="true">↗</span>
    </a>
  );
}

type Job = {
  source: string;
  source_job_id: string;
  company: string;
  title: string;
  location: string | null;
  url: string;
  salary_text: string | null;
  experience_text: string | null;
  selected: boolean;
};

type Params = {
  q?: string | string[];
  company?: string | string[];
  country?: string | string[];
  selected?: string | string[];
  page?: string | string[];
};

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Params>;
}) {
  const params = await searchParams;

  const q = boundedParam(params.q);
  const company = boundedParam(params.company);
  const country = boundedParam(params.country, "Spain");

  const selected = params.selected === "1";

  const requestedPage =
    typeof params.page === "string"
      ? Number.parseInt(params.page, 10)
      : 1;

  const page =
    Number.isFinite(requestedPage) && requestedPage > 0
      ? Math.min(requestedPage, MAX_PAGE)
      : 1;

  const pageSize = 20;
  const offset = (page - 1) * pageSize;

  const [summary, companies, countries, resultCount, results] =
    await Promise.all([
    db.query<{ total: number; selected: number }>(`
      SELECT
        COUNT(*)::int AS total,
        COUNT(*) FILTER (WHERE selected)::int AS selected
      FROM jobs
      WHERE active = true
    `),

    db.query<{ company: string }>(
      "SELECT DISTINCT company FROM jobs WHERE active = true ORDER BY company"
    ),

    db.query<{ country: string }>(`
      SELECT DISTINCT country
      FROM jobs
      CROSS JOIN LATERAL
        unnest(
          COALESCE(
            countries,
            ARRAY[]::text[]
          )
        ) AS c(country)
      WHERE active = true
        AND country <> ''
      ORDER BY country
    `),

    db.query<{ total: number }>(
      `SELECT COUNT(*)::int AS total
      FROM jobs
      WHERE active = true
        AND ($1 = '' OR strpos(lower(title), lower($1)) > 0)
        AND ($2 = '' OR company = $2)
        AND (
          $3 = ''
          OR $3 = ANY(
            COALESCE(
              countries,
              ARRAY[]::text[]
            )
          )
        )
        AND ($4::boolean = false OR selected = true)`,
      [q, company, country, selected]
    ),

    db.query<Job>(
      `SELECT
        source,
        source_job_id,
        company,
        title,
        location,
        url,
        salary_text,
        experience_text,
        selected
      FROM jobs
      WHERE active = true
        AND ($1 = '' OR strpos(lower(title), lower($1)) > 0)
        AND ($2 = '' OR company = $2)
        AND (
          $3 = ''
          OR $3 = ANY(
            COALESCE(
              countries,
              ARRAY[]::text[]
            )
          )
        )
        AND ($4::boolean = false OR selected = true)
      ORDER BY selected DESC, company, title, source_job_id
      LIMIT $5
      OFFSET $6`,
      [q, company, country, selected, pageSize, offset]
    ),
  ]);

  const stats = summary.rows[0];
  const totalResults = resultCount.rows[0].total;
  const totalPages = Math.max(1, Math.ceil(totalResults / pageSize));
  const firstResult = totalResults === 0 ? 0 : offset + 1;
  const lastResult = Math.min(offset + results.rows.length, totalResults);

  const pageHref = (targetPage: number) => {
    const query = new URLSearchParams();

    if (q) query.set("q", q);
    if (company) query.set("company", company);
    query.set("country", country);
    if (selected) query.set("selected", "1");
    if (targetPage > 1) query.set("page", String(targetPage));

    const queryString = query.toString();
    return queryString ? `/?${queryString}` : "/";
  };

  const matchRate =
    stats.total > 0
      ? Math.round((stats.selected / stats.total) * 100)
      : 0;

  return (
    <main className="min-h-screen">
      <section className="hero">
        <div className="mx-auto max-w-6xl px-5 pb-14 pt-8 sm:px-8 sm:pt-10">
          <nav className="mb-16 flex items-center justify-between">
            <div className="flex items-center gap-3">
              <div className="radar-logo">
                <span />
              </div>

              <span className="text-sm font-semibold tracking-tight text-white">
                Job Radar
              </span>
            </div>

            <div className="flex items-center gap-2 text-xs font-medium text-slate-400">
              <span className="status-dot" />
              Actualización automática
            </div>
          </nav>

          <div className="max-w-3xl">
            <p className="mb-4 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-400">
              Oportunidades seleccionadas
            </p>

            <h1 className="max-w-2xl text-4xl font-semibold leading-[1.08] tracking-[-0.04em] text-white sm:text-5xl">
              Encuentra la oportunidad
              <span className="text-slate-400">
                {" "}que merece tu atención.
              </span>
            </h1>

            <p className="mt-5 max-w-xl text-sm leading-6 text-slate-400 sm:text-base">
              Job Radar reúne y filtra automáticamente oportunidades de
              ingeniería de datos en empresas de producto, software y
              FinTechs.
            </p>
          </div>
        </div>
      </section>

      <div className="mx-auto max-w-6xl px-5 pb-16 sm:px-8">
        <section className="stats-grid">
          <div className="stat-card">
            <div>
              <p className="stat-label">Ofertas monitorizadas</p>
              <p className="stat-value">{stats.total}</p>
            </div>

            <div className="stat-icon">01</div>
          </div>

          <div className="stat-card stat-card-highlight">
            <div>
              <p className="stat-label">Coincidencias</p>
              <p className="stat-value">{stats.selected}</p>
            </div>

            <div className="match-rate">{matchRate}%</div>
          </div>

          <div className="stat-card">
            <div>
              <p className="stat-label">Empresas activas</p>
              <p className="stat-value">{companies.rows.length}</p>
            </div>

            <div className="stat-icon">03</div>
          </div>
        </section>

        <section className="mt-12">
          <div className="mb-5 flex flex-col justify-between gap-2 sm:flex-row sm:items-end">
            <div>
              <p className="section-kicker">Radar</p>
              <h2 className="section-title">Oportunidades</h2>
            </div>

            <p className="text-sm text-slate-500">
              {totalResults === 0
                ? "0 resultados"
                : `${firstResult}-${lastResult} de ${totalResults} resultados`}
            </p>
          </div>

          <form className="filter-panel">
            <div className="search-field">
              <svg
                aria-hidden="true"
                viewBox="0 0 24 24"
                className="h-5 w-5 shrink-0"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
              >
                <circle cx="11" cy="11" r="7" />
                <path d="m20 20-3.5-3.5" />
              </svg>

              <input
                name="q"
                defaultValue={q}
                placeholder="Buscar por puesto..."
                aria-label="Buscar por título"
              />
            </div>

            <select
              name="company"
              defaultValue={company}
              aria-label="Filtrar por empresa"
              className="company-select"
            >
              <option value="">Todas las empresas</option>

              {companies.rows.map((item) => (
                <option key={item.company} value={item.company}>
                  {item.company}
                </option>
              ))}
            </select>

            <select
              name="country"
              defaultValue={country}
              aria-label="Filtrar por país"
              className="company-select"
            >
              <option value="">
                Todos los países
              </option>

              {countries.rows.map((item) => (
                <option
                  key={item.country}
                  value={item.country}
                >
                  {item.country}
                </option>
              ))}
            </select>

            <label className="match-toggle">
              <input
                type="checkbox"
                name="selected"
                value="1"
                defaultChecked={selected}
              />

              <span>Solo coincidencias</span>
            </label>

            <button className="primary-button">
              Aplicar filtros
            </button>

            {(q || company || country !== "Spain" || selected) && (
              <a href="/" className="clear-link">
                Limpiar
              </a>
            )}
          </form>

          <div className="mt-5 flex items-center gap-2 text-xs text-slate-500">
            <span className="info-dot" />
            Las coincidencias son una selección preliminar y requieren revisión.
          </div>
        </section>

        <section className="mt-7">
          {results.rows.length === 0 ? (
            <div className="empty-state">
              <div className="empty-icon">
                <svg
                  viewBox="0 0 24 24"
                  className="h-6 w-6"
                  fill="none"
                  stroke="currentColor"
                  strokeWidth="1.6"
                >
                  <circle cx="11" cy="11" r="7" />
                  <path d="m20 20-3.5-3.5" />
                </svg>
              </div>

              <h3>Sin resultados</h3>

              <p>
                Prueba con otra búsqueda o elimina alguno de los filtros.
              </p>
            </div>
          ) : (
            <div className="job-list">
              {results.rows.map((job) => (
                <article
                  key={`${job.source}:${job.source_job_id}`}
                  className={`job-card ${
                    job.selected ? "job-card-selected" : ""
                  }`}
                >
                  <div className="min-w-0">
                    <div className="mb-3 flex flex-wrap items-center gap-2">
                      <span className="company-name">
                        {job.company}
                      </span>

                      {job.selected && (
                        <span className="match-badge">
                          <span />
                          Coincidencia
                        </span>
                      )}
                    </div>

                    <h3 className="job-title">
                      {job.title}
                    </h3>

                    <div className="job-meta">
                      <svg
                        aria-hidden="true"
                        viewBox="0 0 24 24"
                        className="h-4 w-4 shrink-0"
                        fill="none"
                        stroke="currentColor"
                        strokeWidth="1.7"
                      >
                        <path d="M20 10c0 5-8 11-8 11S4 15 4 10a8 8 0 1 1 16 0Z" />
                        <circle cx="12" cy="10" r="2.5" />
                      </svg>

                      <span>
                        {job.location || "Ubicación no indicada"}
                      </span>
                    </div>

                    {(job.salary_text || job.experience_text) && (
                      <div className="mt-3 flex flex-wrap gap-2">
                        {job.salary_text && (
                          <span className="rounded-full bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-800">
                            {job.salary_text}
                          </span>
                        )}

                        {job.experience_text && (
                          <span className="rounded-full bg-slate-100 px-3 py-1 text-xs font-medium text-slate-600">
                            {job.experience_text}
                          </span>
                        )}
                      </div>
                    )}
                  </div>

                  <JobLink url={job.url} />
                </article>
              ))}
            </div>
          )}

          {totalResults > 0 && totalPages > 1 && (
            <nav
              className="mt-8 flex items-center justify-center gap-4"
              aria-label="Paginación de ofertas"
            >
              {page > 1 ? (
                <a
                  href={pageHref(page - 1)}
                  className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:text-slate-950"
                >
                  ← Anterior
                </a>
              ) : (
                <span className="cursor-not-allowed rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium text-slate-300">
                  ← Anterior
                </span>
              )}

              <span className="text-sm text-slate-500">
                Página {page} de {totalPages}
              </span>

              {page < totalPages ? (
                <a
                  href={pageHref(page + 1)}
                  className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:text-slate-950"
                >
                  Siguiente →
                </a>
              ) : (
                <span className="cursor-not-allowed rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium text-slate-300">
                  Siguiente →
                </span>
              )}
            </nav>
          )}
        </section>

        <footer className="mt-14 flex flex-col justify-between gap-2 border-t border-slate-200 pt-6 text-xs text-slate-400 sm:flex-row">
          <p>Job Radar · Personal Data Engineering Job Monitor</p>

          <p>20 ofertas por página</p>
        </footer>
      </div>
    </main>
  );
}
