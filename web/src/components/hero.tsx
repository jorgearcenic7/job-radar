import { RadarLogo } from "@/components/radar-logo";

// El cron corre cada 2 días; más de 3 sin datos indica un pipeline parado.
const UPDATE_OVERDUE_HOURS = 72;

function formatElapsed(seconds: number): string {
  const format = new Intl.RelativeTimeFormat("es", { numeric: "auto" });
  const hours = Math.floor(seconds / 3600);

  if (hours < 1) {
    return format.format(-Math.max(1, Math.floor(seconds / 60)), "minute");
  }
  if (hours < 48) {
    return format.format(-hours, "hour");
  }
  return format.format(-Math.floor(hours / 24), "day");
}

type UpdateStatusProps = {
  secondsSinceUpdate: number | null;
};

function UpdateStatus({ secondsSinceUpdate }: UpdateStatusProps) {
  const overdue =
    secondsSinceUpdate === null ||
    secondsSinceUpdate > UPDATE_OVERDUE_HOURS * 3600;

  return (
    <div
      className={`flex items-center gap-2 text-xs font-medium ${
        overdue ? "text-amber-300" : "text-slate-400"
      }`}
    >
      <span className={`status-dot ${overdue ? "status-dot-stale" : ""}`} />
      {secondsSinceUpdate === null
        ? "Sin datos de actualización"
        : `Actualizado ${formatElapsed(secondsSinceUpdate)}`}
    </div>
  );
}

type HeroProps = UpdateStatusProps;

export function Hero({ secondsSinceUpdate }: HeroProps) {
  return (
    <section className="hero">
      <div className="mx-auto max-w-6xl px-5 pb-14 pt-8 sm:px-8 sm:pt-10">
        <nav className="mb-16 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <RadarLogo />

            <span className="text-sm font-semibold tracking-tight text-white">
              Job Radar
            </span>
          </div>

          <UpdateStatus secondsSinceUpdate={secondsSinceUpdate} />
        </nav>

        <div className="max-w-3xl">
          <p className="mb-4 text-xs font-semibold uppercase tracking-[0.22em] text-emerald-400">
            Oportunidades seleccionadas
          </p>

          <h1 className="max-w-2xl text-4xl font-semibold leading-[1.08] tracking-[-0.04em] text-white sm:text-5xl">
            Encuentra la oportunidad
            <span className="text-slate-400">{" "}que merece tu atención.</span>
          </h1>

          <p className="mt-5 max-w-xl text-sm leading-6 text-slate-400 sm:text-base">
            Job Radar reúne y filtra automáticamente oportunidades de
            ingeniería de datos en empresas de producto, software y FinTechs.
          </p>
        </div>
      </div>
    </section>
  );
}
