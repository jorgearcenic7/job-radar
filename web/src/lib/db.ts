import "server-only";
import { Pool } from "pg";

const connectionString =
  process.env.WEB_DATABASE_URL ??
  (process.env.VERCEL === "1" ? undefined : process.env.DATABASE_URL);

if (!connectionString) {
  throw new Error(
    process.env.VERCEL === "1"
      ? "WEB_DATABASE_URL is not configured"
      : "WEB_DATABASE_URL or DATABASE_URL is not configured",
  );
}

const globalForDb = globalThis as unknown as {
  jobRadarPool: Pool | undefined;
};

export const db =
  globalForDb.jobRadarPool ??
  new Pool({
    connectionString,
    max: 5,
    connectionTimeoutMillis: 10000,
    idleTimeoutMillis: 30000,
    query_timeout: 10000,
    statement_timeout: 10000,
  });

if (process.env.NODE_ENV !== "production") {
  globalForDb.jobRadarPool = db;
}
