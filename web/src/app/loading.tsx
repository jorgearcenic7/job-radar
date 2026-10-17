export default function Loading() {
  return (
    <main className="mx-auto flex min-h-screen max-w-6xl items-center justify-center px-5">
      <div
        role="status"
        className="flex items-center gap-3 text-sm text-slate-500"
      >
        <div className="radar-logo">
          <span />
        </div>
        Cargando oportunidades…
      </div>
    </main>
  );
}
