import { db } from "@/lib/db";

export const dynamic = "force-dynamic";

type Job = {
  source: string;
  source_job_id: string;
  company: string;
  title: string;
  location: string | null;
  url: string;
  selected: boolean;
};

type Params = {
  q?: string | string[];
  company?: string | string[];
  selected?: string | string[];
};

export default async function Home({
  searchParams,
}: {
  searchParams: Promise<Params>;
}) {
  const params = await searchParams;
  const q = typeof params.q === "string" ? params.q.trim() : "";
  const company =
    typeof params.company === "string" ? params.company : "";
  const selected = params.selected === "1";

  const [summary, companies, results] = await Promise.all([
    db.query<{ total: number; selected: number }>(`
      SELECT COUNT(*)::int AS total,
             COUNT(*) FILTER (WHERE selected)::int AS selected
      FROM jobs
    `),
    db.query<{ company: string }>(
      "SELECT DISTINCT company FROM jobs ORDER BY company"
    ),
    db.query<Job>(
      `SELECT source, source_job_id, company, title, location, url, selected
       FROM jobs
       WHERE ($1 = '' OR strpos(lower(title), lower($1)) > 0)
         AND ($2 = '' OR company = $2)
         AND ($3::boolean = false OR selected = true)
       ORDER BY company, title, source_job_id
       LIMIT 100`,
      [q, company, selected]
    ),
  ]);

  const stats = summary.rows[0];

  return (
    <main className="mx-auto max-w-6xl px-6 py-12">
      <header className="mb-10">
        <p className="mb-3 text-sm font-semibold uppercase tracking-widest text-emerald-700">
          Tu próximo paso
        </p>
        <h1 className="text-4xl font-bold tracking-tight">Job Radar</h1>
        <p className="mt-3 text-slate-600">
          Explora oportunidades en fintech y empresas de software.
        </p>
      </header>

      <section className="mb-8 grid gap-4 sm:grid-cols-3">
        {[
          ["Ofertas guardadas", stats.total],
          ["Coincidencias del filtro", stats.selected],
          ["Empresas", companies.rows.length],
        ].map(([label, value]) => (
          <div key={label} className="rounded-2xl border border-slate-200 bg-white p-6">
            <p className="text-sm text-slate-600">{label}</p>
            <p className="mt-2 text-3xl font-bold">{value}</p>
          </div>
        ))}
      </section>

      <form className="mb-8 flex flex-wrap items-end gap-4 rounded-2xl bg-white p-5">
        <label className="flex min-w-56 flex-1 flex-col gap-2 text-sm font-medium">
          Buscar por título
          <input
            name="q"
            defaultValue={q}
            placeholder="Data, backend, analytics..."
            className="rounded-lg border border-slate-300 p-3"
          />
        </label>

        <label className="flex flex-col gap-2 text-sm font-medium">
          Empresa
          <select
            name="company"
            defaultValue={company}
            className="rounded-lg border border-slate-300 p-3"
          >
            <option value="">Todas las empresas</option>
            {companies.rows.map((item) => (
              <option key={item.company} value={item.company}>
                {item.company}
              </option>
            ))}
          </select>
        </label>

        <label className="flex items-center gap-2 py-3 text-sm">
          <input type="checkbox" name="selected" value="1" defaultChecked={selected} />
          Solo coincidencias
        </label>

        <button className="rounded-lg bg-emerald-700 px-5 py-3 font-semibold text-white hover:bg-emerald-800">
          Aplicar
        </button>
        <a href="/" className="px-2 py-3 text-sm underline">
          Limpiar
        </a>
      </form>

      <p className="mb-4 text-sm text-slate-600">
        Mostrando {results.rows.length} ofertas. Máximo 100 por consulta.
        Las coincidencias requieren revisión de experiencia y ubicación.
      </p>

      <section className="space-y-3">
        {results.rows.length === 0 ? (
          <div className="rounded-2xl border border-dashed border-slate-300 p-10 text-center">
            <h2 className="text-lg font-semibold">Sin resultados con estos filtros</h2>
            <p className="mt-2 text-slate-600">
              Prueba otra búsqueda o desmarca «Solo coincidencias».
            </p>
          </div>
        ) : (
          results.rows.map((job) => (
            <article
              key={`${job.source}:${job.source_job_id}`}
              className="flex flex-col justify-between gap-4 rounded-2xl border border-slate-200 bg-white p-6 sm:flex-row sm:items-center"
            >
              <div>
                <p className="text-sm font-semibold text-emerald-700">{job.company}</p>
                <h2 className="mt-1 text-lg font-semibold">{job.title}</h2>
                <p className="mt-2 text-sm text-slate-600">
                  {job.location || "Ubicación no indicada"}
                </p>
                {job.selected && (
                  <p className="mt-2 text-xs font-medium text-emerald-700">
                    Coincidencia preliminar
                  </p>
                )}
              </div>
              <a
                href={job.url}
                target="_blank"
                rel="noopener noreferrer"
                className="shrink-0 font-semibold text-emerald-700 underline"
              >
                Ver oferta ↗
              </a>
            </article>
          ))
        )}
      </section>

      <footer className="mt-10 text-sm text-slate-500">
        Datos de la última carga manual. Algunas ofertas guardadas podrían
        haber cerrado desde entonces.
      </footer>
    </main>
  );
}
