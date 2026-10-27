import Link from "next/link";

import { SearchIcon } from "@/components/icons";
import { hasActiveFilters, type JobFilters } from "@/lib/job-search";

type FilterFormProps = {
  filters: JobFilters;
  companies: string[];
  countries: string[];
};

export function FilterForm({ filters, companies, countries }: FilterFormProps) {
  // Los campos no están controlados: cambiar la key cuando cambian los filtros
  // remonta el formulario para que los defaultValue reflejen la URL actual.
  return (
    <form key={JSON.stringify(filters)} className="filter-panel">
      <div className="search-field">
        <SearchIcon className="h-5 w-5 shrink-0" strokeWidth="1.8" />

        <input
          name="q"
          defaultValue={filters.q}
          placeholder="Buscar por puesto..."
          aria-label="Buscar por título"
        />
      </div>

      <select
        name="company"
        defaultValue={filters.company}
        aria-label="Filtrar por empresa"
        className="company-select"
      >
        <option value="">Todas las empresas</option>
        {companies.map((company) => (
          <option key={company} value={company}>
            {company}
          </option>
        ))}
      </select>

      <select
        name="country"
        defaultValue={filters.country}
        aria-label="Filtrar por país"
        className="company-select"
      >
        <option value="">Todos los países</option>
        {countries.map((country) => (
          <option key={country} value={country}>
            {country}
          </option>
        ))}
      </select>

      <label className="match-toggle">
        <input
          type="checkbox"
          name="selected"
          value="1"
          defaultChecked={filters.selected}
        />
        <span>Solo coincidencias</span>
      </label>

      <button className="primary-button">Aplicar filtros</button>

      {hasActiveFilters(filters) && (
        <Link href="/" className="clear-link">
          Limpiar
        </Link>
      )}
    </form>
  );
}
