import "server-only";

import { db } from "@/lib/db";
import { PAGE_SIZE, type JobFilters } from "@/lib/job-search";

// Ofertas sin ver en este tiempo respecto a la última ingesta pertenecen a
// fuentes que fallan: su enlace ya no está verificado. Mantener sincronizado
// con STALE_AFTER_DAYS en job_radar/storage/postgres.py.
const STALE_AFTER_DAYS = 7;
const NEW_JOB_DAYS = 3;

// Condiciones de los filtros del usuario. Ocupan siempre $1-$4 en el orden de
// filterParams(); cada consulta numera a partir de $5 sus propios parámetros.
const USER_FILTERS_SQL = `
  ($1 = '' OR strpos(lower(title), lower($1)) > 0)
  AND ($2 = '' OR company = $2)
  AND ($3 = '' OR $3 = ANY(COALESCE(countries, ARRAY[]::text[])))
  AND ($4::boolean = false OR selected = true)`;

function filterParams(filters: JobFilters): [string, string, string, boolean] {
  return [filters.q, filters.company, filters.country, filters.selected];
}

export type Job = {
  source: string;
  source_job_id: string;
  company: string;
  title: string;
  location: string | null;
  url: string;
  salary_text: string | null;
  experience_text: string | null;
  selected: boolean;
  match_status: string | null;
  is_new: boolean;
};

export type JobBoard = {
  secondsSinceUpdate: number | null;
  totalJobs: number;
  selectedJobs: number;
  companies: string[];
  countries: string[];
  totalResults: number;
  jobs: Job[];
};

export async function getJobBoard(
  filters: JobFilters,
  offset: number,
): Promise<JobBoard> {
  const freshness = await db.query<{
    cutoff: Date | null;
    seconds_since_update: number | null;
  }>(
    `SELECT
      MAX(last_seen_at) - make_interval(days => $1) AS cutoff,
      EXTRACT(EPOCH FROM CURRENT_TIMESTAMP - MAX(last_seen_at))::int
        AS seconds_since_update
    FROM jobs
    WHERE active = true`,
    [STALE_AFTER_DAYS],
  );
  const { cutoff, seconds_since_update: secondsSinceUpdate } =
    freshness.rows[0];

  const [summary, companies, countries, resultCount, results] =
    await Promise.all([
      db.query<{ total: number; selected: number }>(
        `SELECT
          COUNT(*)::int AS total,
          COUNT(*) FILTER (WHERE selected)::int AS selected
        FROM jobs
        WHERE active = true
          AND last_seen_at >= $1`,
        [cutoff],
      ),

      db.query<{ company: string }>(
        `SELECT DISTINCT company
        FROM jobs
        WHERE active = true
          AND last_seen_at >= $1
        ORDER BY company`,
        [cutoff],
      ),

      db.query<{ country: string }>(
        `SELECT DISTINCT country
        FROM jobs
        CROSS JOIN LATERAL
          unnest(COALESCE(countries, ARRAY[]::text[])) AS c(country)
        WHERE active = true
          AND last_seen_at >= $1
          AND country <> ''
        ORDER BY country`,
        [cutoff],
      ),

      db.query<{ total: number }>(
        `SELECT COUNT(*)::int AS total
        FROM jobs
        WHERE active = true
          AND last_seen_at >= $5
          AND ${USER_FILTERS_SQL}`,
        [...filterParams(filters), cutoff],
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
          selected,
          match_status,
          first_seen_at >= CURRENT_TIMESTAMP - make_interval(days => $8)
            AS is_new
        FROM jobs
        WHERE active = true
          AND last_seen_at >= $7
          AND ${USER_FILTERS_SQL}
        ORDER BY selected DESC, company, title, source_job_id
        LIMIT $5
        OFFSET $6`,
        [...filterParams(filters), PAGE_SIZE, offset, cutoff, NEW_JOB_DAYS],
      ),
    ]);

  return {
    secondsSinceUpdate,
    totalJobs: summary.rows[0].total,
    selectedJobs: summary.rows[0].selected,
    companies: companies.rows.map((row) => row.company),
    countries: countries.rows.map((row) => row.country),
    totalResults: resultCount.rows[0].total,
    jobs: results.rows,
  };
}
