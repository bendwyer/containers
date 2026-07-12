import { randomUUID } from 'node:crypto';
import { Hono } from 'hono';
import { z } from 'zod';
import type { Db } from './db.js';
import type { Regions } from './regions.js';
import type { ProvisionParams } from './saga.js';
import type { PruneSummary } from './reaper.js';
import type { MetricsRenderer } from './metrics.js';

// HTTP routes (Hono). Dependencies (database, region catalog, saga kickoff,
// reaper) are injected via ServerDeps, so the routes stay thin and can be tested
// with app.request() without a real server, database, or cloud calls.

// TTL is required and capped at 30 days; the reaper destroys a node once its
// expires_at has passed.
const MAX_TTL_HOURS = 24 * 30;

const DeployRequestSchema = z.object({
  // Friendly region slug from the catalog (e.g. 'frankfurt').
  region: z.string().min(1),
  // Becomes part of the node hostname; keep it DNS-label friendly.
  name: z
    .string()
    .min(1)
    .max(32)
    .regex(/^[a-z0-9][a-z0-9-]*$/, 'name must be lowercase letters, digits, and dashes'),
  ttl_hours: z.number().int().min(1).max(MAX_TTL_HOURS),
});

export interface ServerDeps {
  db: Db;
  regions: Regions;
  /** Fire-and-forget kickoff of the provisioning saga. */
  startSaga: (params: ProvisionParams) => void;
  /** Run one reaper sweep (the on-demand twin of the periodic sweep). */
  runPrune: () => Promise<PruneSummary>;
  /** Renders the Prometheus exposition served at GET /metrics. */
  metrics: MetricsRenderer;
}

export function createApp(deps: ServerDeps): Hono {
  const app = new Hono();

  // Liveness: the process is up. Kept DB-free so a transient DB blip doesn't
  // trigger a pod restart (readiness, below, gates traffic instead).
  app.get('/healthz', (c) => c.json({ status: 'ok' }));

  // Readiness: the DB is reachable. Wired to the readiness probe so traffic is
  // held off (not the pod restarted) while the DB is unavailable.
  app.get('/readyz', async (c) => {
    try {
      await deps.db.ping();
      return c.json({ status: 'ready' });
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      return c.json({ status: 'unready', error: message }, 503);
    }
  });

  app.post('/deploy', async (c) => {
    let body: unknown;
    try {
      body = await c.req.json();
    } catch {
      return c.json({ error: 'request body must be JSON' }, 400);
    }

    const parsed = DeployRequestSchema.safeParse(body);
    if (!parsed.success) {
      return c.json({ error: parsed.error.issues.map((issue) => issue.message).join('; ') }, 400);
    }
    const { region: slug, name, ttl_hours: ttlHours } = parsed.data;

    const catalog = await deps.regions.getCatalog();
    const entry = catalog[slug];
    if (!entry) {
      return c.json({ error: `unknown or unavailable region: ${slug}` }, 404);
    }

    const id = randomUUID();
    const hostname = `${name}-${id.slice(0, 8)}`;
    const createdAt = Date.now();
    const expiresAt = createdAt + ttlHours * 3_600_000;

    await deps.db.insertDeployment({
      id,
      name,
      regionSlug: slug,
      provider: entry.provider,
      providerRegion: entry.region,
      hostname,
      createdAt,
      expiresAt,
    });

    deps.startSaga({
      deploymentId: id,
      provider: entry.provider,
      providerRegion: entry.region,
      name,
      hostname,
      expiresAt,
    });

    // The API is private (fronted by a separate UI); log who asked, for
    // attribution. A front-end can pass the acting user via X-Requested-By. Not
    // verified here.
    const requestedBy = c.req.header('X-Requested-By') ?? 'unknown';
    console.log(
      `deploy ${id} region=${slug} provider=${entry.provider} ttl=${ttlHours}h by=${requestedBy}`,
    );

    return c.json({ id, status: 'provisioning', hostname, expires_at: expiresAt }, 202);
  });

  app.get('/list', async (c) => {
    return c.json({ deployments: await deps.db.listDeployments() });
  });

  app.get('/regions', async (c) => {
    return c.json(await deps.regions.getCatalog());
  });

  // Prometheus metrics. No auth, like the rest of this internal API.
  app.get('/metrics', async (c) => {
    const body = await deps.metrics.render();
    return c.text(body, 200, { 'content-type': deps.metrics.contentType });
  });

  // On-demand twin of the reaper's periodic sweep, same reconciliation,
  // triggered by hand instead of waiting for the next tick.
  app.post('/prune', async (c) => {
    const summary = await deps.runPrune();
    console.log(`prune ${JSON.stringify(summary)}`);
    return c.json(summary);
  });

  app.delete('/deployments/:id', async (c) => {
    // Clamp the deployment's expiry to now; the reaper tears it down on its next
    // sweep. Clamp rather than overwrite so a sooner natural expiry stands.
    const now = Date.now();
    const changed = await deps.db.clampExpiry(c.req.param('id'), now);
    if (!changed) {
      return c.json({ error: 'not found' }, 404);
    }
    return c.json({ id: c.req.param('id'), status: 'expiring', expires_at: now }, 202);
  });

  return app;
}
