import { serve } from '@hono/node-server';
import { Pool, types } from 'pg';
import { loadConfig } from './config.js';
import { createDb } from './db.js';
import { createMetrics } from './metrics.js';
import { prune, startReaper, type PruneSummary } from './reaper.js';
import { createRegions } from './regions.js';
import { provisionSaga } from './saga.js';
import { createApp } from './server.js';

// Entry point: load config -> connect Postgres -> migrate -> startup-reconcile ->
// serve the API -> start the reaper sweep. Logs go to stdout; Prometheus metrics
// are exposed at GET /metrics (scraped by the cluster collector); no log shipping.

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
  const metrics = createMetrics(db);

  // One sweep-and-record path shared by the periodic reaper and the on-demand
  // /prune route. The periodic sweep logs its summary (as before); the manual
  // prune is logged by the route handler.
  const runSweep = async (trigger: 'periodic' | 'manual'): Promise<PruneSummary> => {
    const start = Date.now();
    try {
      const summary = await prune(deps);
      metrics.recordSweep(trigger, summary, Date.now() - start);
      if (trigger === 'periodic') console.log(`reaper sweep ${JSON.stringify(summary)}`);
      return summary;
    } catch (err) {
      metrics.recordSweepError(trigger, Date.now() - start);
      throw err;
    }
  };

  const app = createApp({
    db,
    regions,
    metrics,
    startSaga: (params) => void provisionSaga(deps, params),
    runPrune: () => runSweep('manual'),
  });

  const server = serve({ fetch: app.fetch, port: config.port }, (info) => {
    console.log(`tailscale-exit-nodes listening on :${info.port}`);
  });

  const stopReaper = startReaper(() => runSweep('periodic'));

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
