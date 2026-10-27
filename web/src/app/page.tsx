import { redirect } from "next/navigation";

import { FilterForm } from "@/components/filter-form";
import { Hero } from "@/components/hero";
import { JobList } from "@/components/job-list";
import { Pagination } from "@/components/pagination";
import { StatsGrid } from "@/components/stats-grid";
import {
  PAGE_SIZE,
  jobSearchHref,
  parseFilters,
  parsePage,
} from "@/lib/job-search";
import { getJobBoard } from "@/lib/jobs";

export const dynamic = "force-dynamic";

export default async function Home({ searchParams }: PageProps<"/">) {
  const params = await searchParams;
  const filters = parseFilters(params);
  const page = parsePage(params.page);
  const offset = (page - 1) * PAGE_SIZE;

  const board = await getJobBoard(filters, offset);
  const { totalResults, jobs } = board;
  const totalPages = Math.max(1, Math.ceil(totalResults / PAGE_SIZE));

  if (page > totalPages) {
    redirect(jobSearchHref(filters, totalPages));
  }

  const firstResult = offset + 1;
  const lastResult = Math.min(offset + jobs.length, totalResults);

  return (
    <main className="min-h-screen">
      <Hero secondsSinceUpdate={board.secondsSinceUpdate} />

      <div className="mx-auto max-w-6xl px-5 pb-16 sm:px-8">
        <StatsGrid
          totalJobs={board.totalJobs}
          selectedJobs={board.selectedJobs}
          companyCount={board.companies.length}
        />

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

          <FilterForm
            filters={filters}
            companies={board.companies}
            countries={board.countries}
          />

          <div className="mt-5 flex items-center gap-2 text-xs text-slate-500">
            <span className="info-dot" />
            Las coincidencias son una selección preliminar y requieren revisión.
          </div>
        </section>

        <section className="mt-7">
          <JobList jobs={jobs} />
          <Pagination filters={filters} page={page} totalPages={totalPages} />
        </section>

        <footer className="mt-14 flex flex-col justify-between gap-2 border-t border-slate-200 pt-6 text-xs text-slate-500 sm:flex-row">
          <p>Job Radar · Personal Data Engineering Job Monitor</p>
          <p>{PAGE_SIZE} ofertas por página</p>
        </footer>
      </div>
    </main>
  );
}
