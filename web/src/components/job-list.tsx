import { LocationIcon, SearchIcon } from "@/components/icons";
import type { Job } from "@/lib/jobs";

function safeExternalUrl(value: string): string | null {
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

function MatchBadge({ status }: { status: string | null }) {
  return (
    <span
      className={`match-badge ${
        status === "Stretch" ? "match-badge-stretch" : ""
      }`}
    >
      <span />
      {status ?? "Coincidencia"}
    </span>
  );
}

function JobCard({ job }: { job: Job }) {
  return (
    <article className={`job-card ${job.selected ? "job-card-selected" : ""}`}>
      <div className="min-w-0">
        <div className="mb-3 flex flex-wrap items-center gap-2">
          <span className="company-name">{job.company}</span>
          {job.selected && <MatchBadge status={job.match_status} />}
          {job.is_new && <span className="new-badge">Nueva</span>}
        </div>

        <h3 className="job-title">{job.title}</h3>

        <div className="job-meta">
          <LocationIcon />
          <span>{job.location || "Ubicación no indicada"}</span>
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
  );
}

function EmptyState() {
  return (
    <div className="empty-state">
      <div className="empty-icon">
        <SearchIcon className="h-6 w-6" strokeWidth="1.6" />
      </div>
      <h3>Sin resultados</h3>
      <p>Prueba con otra búsqueda o elimina alguno de los filtros.</p>
    </div>
  );
}

export function JobList({ jobs }: { jobs: Job[] }) {
  if (jobs.length === 0) {
    return <EmptyState />;
  }

  return (
    <div className="job-list">
      {jobs.map((job) => (
        <JobCard key={`${job.source}:${job.source_job_id}`} job={job} />
      ))}
    </div>
  );
}
