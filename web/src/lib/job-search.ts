export const DEFAULT_COUNTRY = "Spain";
export const PAGE_SIZE = 20;

const MAX_FILTER_LENGTH = 120;
const MAX_PAGE = 500;

type SearchParamValue = string | string[] | undefined;
type SearchParams = Record<string, SearchParamValue>;

export type JobFilters = {
  q: string;
  company: string;
  country: string;
  selected: boolean;
};

function boundedParam(value: SearchParamValue, fallback = ""): string {
  return typeof value === "string"
    ? value.trim().slice(0, MAX_FILTER_LENGTH)
    : fallback;
}

export function parseFilters(params: SearchParams): JobFilters {
  return {
    q: boundedParam(params.q),
    company: boundedParam(params.company),
    country: boundedParam(params.country, DEFAULT_COUNTRY),
    selected: params.selected === "1",
  };
}

export function parsePage(value: SearchParamValue): number {
  const requestedPage =
    typeof value === "string" ? Number.parseInt(value, 10) : 1;

  return Number.isFinite(requestedPage) && requestedPage > 0
    ? Math.min(requestedPage, MAX_PAGE)
    : 1;
}

export function hasActiveFilters(filters: JobFilters): boolean {
  return Boolean(
    filters.q ||
      filters.company ||
      filters.country !== DEFAULT_COUNTRY ||
      filters.selected,
  );
}

export function jobSearchHref(filters: JobFilters, page: number): string {
  const query = new URLSearchParams();

  if (filters.q) query.set("q", filters.q);
  if (filters.company) query.set("company", filters.company);
  // El país se envía siempre, incluso vacío: sin el parámetro se aplicaría
  // DEFAULT_COUNTRY en lugar de "Todos los países".
  query.set("country", filters.country);
  if (filters.selected) query.set("selected", "1");
  if (page > 1) query.set("page", String(page));

  return `/?${query}`;
}
