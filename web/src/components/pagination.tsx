import Link from "next/link";
import type { ReactNode } from "react";

import { jobSearchHref, type JobFilters } from "@/lib/job-search";

type PageLinkProps = {
  href: string | null;
  children: ReactNode;
};

function PageLink({ href, children }: PageLinkProps) {
  if (href === null) {
    return (
      <span className="cursor-not-allowed rounded-lg border border-slate-200 bg-slate-50 px-4 py-2 text-sm font-medium text-slate-300">
        {children}
      </span>
    );
  }

  return (
    <Link
      href={href}
      className="rounded-lg border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition hover:border-slate-400 hover:text-slate-950"
    >
      {children}
    </Link>
  );
}

type PaginationProps = {
  filters: JobFilters;
  page: number;
  totalPages: number;
};

export function Pagination({ filters, page, totalPages }: PaginationProps) {
  if (totalPages <= 1) {
    return null;
  }

  const previousHref = page > 1 ? jobSearchHref(filters, page - 1) : null;
  const nextHref =
    page < totalPages ? jobSearchHref(filters, page + 1) : null;

  return (
    <nav
      className="mt-8 flex items-center justify-center gap-4"
      aria-label="Paginación de ofertas"
    >
      <PageLink href={previousHref}>← Anterior</PageLink>

      <span className="text-sm text-slate-500">
        Página {page} de {totalPages}
      </span>

      <PageLink href={nextHref}>Siguiente →</PageLink>
    </nav>
  );
}
