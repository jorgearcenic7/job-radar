import "server-only";
import { Pool } from "pg";

const globalForDb = globalThis as unknown as {
  jobRadarPool: Pool | undefined;
};

export const db =
  globalForDb.jobRadarPool ??
  new Pool({
    max: 5,
    connectionTimeoutMillis: 10000,
    idleTimeoutMillis: 30000,
  });

if (process.env.NODE_ENV !== "production") {
  globalForDb.jobRadarPool = db;
}
