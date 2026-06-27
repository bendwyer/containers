import { serve } from '@hono/node-server';
import { Pool, types } from 'pg';
import { loadConfig } from './config.js';
import { createDb } from './db.js';
import { prune, startReaper } from './reaper.js';
import { createRegions } from './regions.js';
import { provisionSaga } from './saga.js';
import { createApp } from './server.js';

// Entry point: load config -> connect Postgres -> migrate -> startup-reconcile ->
// serve the API -> start the reaper sweep. Logs go to stdout; v1 ships no metrics
// or log shipping.

// BIGINT (int8, oid 20) arrives from node-pg as a string by default; coerce to
// number. created_at/expires_at are unix-ms, well within Number.MAX_SAFE_INTEGER.
types.setTypeParser(20, (value) => Number(value));

async function main(): Promise<void> {
  const config = loadConfig();

  const pool = new Pool({ connectionString: config.databaseUrl });
  const db = createDb(pool);
  await db.migrate();
  const reconciled = await db.reconcileStartup();
  if (reconciled > 0) {
    console.log(`startup reconcile: ${reconciled} stale provisioning row(s) marked failed`);
  }

  const regions = createRegions();
  const deps = { creds: config.cloud, db };

  const app = createApp({
    db,
    regions,
    startSaga: (params) => void provisionSaga(deps, params),
    runPrune: () => prune(deps),
  });

  const server = serve({ fetch: app.fetch, port: config.port }, (info) => {
    console.log(`tailscale-exit-nodes listening on :${info.port}`);
  });

  const stopReaper = startReaper(deps);

  const shutdown = (signal: string) => {
    console.log(`received ${signal}, shutting down`);
    stopReaper();
    server.close();
    void pool.end().finally(() => process.exit(0));
  };
  process.on('SIGTERM', () => shutdown('SIGTERM'));
  process.on('SIGINT', () => shutdown('SIGINT'));
}

main().catch((err) => {
  console.error(`fatal: ${err instanceof Error ? (err.stack ?? err.message) : String(err)}`);
  process.exit(1);
});
