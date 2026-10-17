"use client";

import { useEffect } from "react";

export default function Error({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  useEffect(() => {
    console.error(error);
  }, [error]);

  return (
    <main className="mx-auto flex min-h-screen max-w-xl items-center px-5">
      <div className="empty-state w-full">
        <h3>No se pudieron cargar las ofertas</h3>
        <p>
          Si la base de datos estaba en reposo, suele resolverse al reintentar
          en unos segundos.
        </p>
        {error.digest && <p>Referencia: {error.digest}</p>}
        <button className="primary-button mt-5" onClick={() => retry()}>
          Reintentar
        </button>
      </div>
    </main>
  );
}
