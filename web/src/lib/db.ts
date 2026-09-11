import "server-only";
import { Pool } from "pg";

if (!process.env.DATABASE_URL) {
  throw new Error("DATABASE_URL is not configured");
}

const globalForDb = globalThis as unknown as {
  jobRadarPool: Pool | undefined;
};

export const db =
  globalForDb.jobRadarPool ??
  new Pool({
    connectionString: process.env.DATABASE_URL,
    max: 5,
    connectionTimeoutMillis: 10000,
    idleTimeoutMillis: 30000,
  });

if (process.env.NODE_ENV !== "production") {
  globalForDb.jobRadarPool = db;
}
