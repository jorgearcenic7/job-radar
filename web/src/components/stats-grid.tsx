import type { ReactNode } from "react";

function formatMatchRate(selected: number, total: number): string {
  const percent = total > 0 ? (selected / total) * 100 : 0;
  // Con miles de ofertas y pocas coincidencias, redondear daría un engañoso
  // "0%" aunque existan coincidencias.
  if (percent > 0 && percent < 1) {
    return "<1";
  }
  return String(Math.round(percent));
}

type StatCardProps = {
  label: string;
  value: number;
  badge: ReactNode;
  highlight?: boolean;
};

function StatCard({ label, value, badge, highlight = false }: StatCardProps) {
  return (
    <div className={highlight ? "stat-card stat-card-highlight" : "stat-card"}>
      <div>
        <p className="stat-label">{label}</p>
        <p className="stat-value">{value}</p>
      </div>

      {badge}
    </div>
  );
}

type StatsGridProps = {
  totalJobs: number;
  selectedJobs: number;
  companyCount: number;
};

export function StatsGrid({
  totalJobs,
  selectedJobs,
  companyCount,
}: StatsGridProps) {
  return (
    <section className="stats-grid">
      <StatCard
        label="Ofertas monitorizadas"
        value={totalJobs}
        badge={<div className="stat-icon">01</div>}
      />

      <StatCard
        label="Coincidencias"
        value={selectedJobs}
        badge={
          <div className="match-rate">
            {formatMatchRate(selectedJobs, totalJobs)}%
          </div>
        }
        highlight
      />

      <StatCard
        label="Empresas activas"
        value={companyCount}
        badge={<div className="stat-icon">03</div>}
      />
    </section>
  );
}
