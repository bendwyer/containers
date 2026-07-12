import { Counter, Gauge, Histogram, Registry } from 'prom-client';
import type { Db } from './db.js';
import type { Provider } from './lib/reaper-core.js';
import type { PruneSummary } from './reaper.js';

// Prometheus metrics for the control plane. Two families:
//   - ledger gauges, rebuilt from the DB on each scrape (current state), so a
//     terminal node's series drops out on the next scrape rather than going
//     stale; and
//   - reaper counters, accumulated over the process lifetime from each sweep.
// The GET /metrics route calls render(); the reaper and the on-demand /prune
// route call recordSweep()/recordSweepError(). Metric names are Prometheus-
// native so a collector scrapes them with no OTLP name/unit translation.

export type SweepTrigger = 'periodic' | 'manual';

// Emit a zeroed leak series per provider so exit_node_expired_active always
// exists to alert on, even when nothing has leaked. Typed against Provider so a
// new provider is a compile error here.
const PROVIDERS: readonly Provider[] = ['aws', 'vultr'];

/** The slice of the metrics object the HTTP layer needs to serve GET /metrics. */
export interface MetricsRenderer {
  contentType: string;
  render: () => Promise<string>;
}

export type Metrics = ReturnType<typeof createMetrics>;

export function createMetrics(db: Db) {
  const registry = new Registry();

  const deployments = new Gauge({
    name: 'exit_node_deployments',
    help: 'Deployments in the ledger by status, provider, and region.',
    labelNames: ['status', 'provider', 'region'],
    registers: [registry],
  });

  const expiresAt = new Gauge({
    name: 'exit_node_expires_at_seconds',
    help: 'Unix expiry per live (provisioning/active) deployment; TTL remaining is this minus time().',
    labelNames: ['id', 'name', 'provider', 'region'],
    registers: [registry],
  });

  const expiredActive = new Gauge({
    name: 'exit_node_expired_active',
    help: 'Active deployments already past expires_at (the reaper should have destroyed them).',
    labelNames: ['provider'],
    registers: [registry],
  });

  const sweeps = new Counter({
    name: 'exit_node_reaper_sweeps_total',
    help: 'Reaper sweeps run, by trigger and outcome.',
    labelNames: ['trigger', 'outcome'],
    registers: [registry],
  });

  const reaped = new Counter({
    name: 'exit_node_reaper_reaped_total',
    help: 'Instances destroyed by the reaper, by provider.',
    labelNames: ['provider'],
    registers: [registry],
  });

  const reconciled = new Counter({
    name: 'exit_node_reaper_reconciled_total',
    help: 'Ledger rows reconciled (their instance was already gone) by the reaper.',
    registers: [registry],
  });

  const sweepErrors = new Counter({
    name: 'exit_node_reaper_errors_total',
    help: 'Per-item errors recorded during reaper sweeps, by scope.',
    labelNames: ['scope'],
    registers: [registry],
  });

  const lastSweep = new Gauge({
    name: 'exit_node_reaper_last_sweep_timestamp_seconds',
    help: 'Unix time of the last reaper sweep, by trigger; alert on the periodic series going stale.',
    labelNames: ['trigger'],
    registers: [registry],
  });

  const sweepDuration = new Histogram({
    name: 'exit_node_reaper_sweep_duration_seconds',
    help: 'Reaper sweep wall-clock duration, by trigger.',
    labelNames: ['trigger'],
    buckets: [0.5, 1, 2, 5, 10, 30, 60],
    registers: [registry],
  });

  return {
    contentType: registry.contentType,

    /** Rebuild the ledger gauges from a fresh DB snapshot, then serialize. */
    async render(): Promise<string> {
      const snap = await db.metricsSnapshot();

      deployments.reset();
      for (const c of snap.counts) {
        deployments.set({ status: c.status, provider: c.provider, region: c.region }, c.n);
      }

      const now = Date.now();
      const leaks = new Map<string, number>();
      expiresAt.reset();
      for (const row of snap.live) {
        expiresAt.set(
          { id: row.id, name: row.name, provider: row.provider, region: row.region },
          Math.round(row.expiresAt / 1000),
        );
        if (row.status === 'active' && row.expiresAt <= now) {
          leaks.set(row.provider, (leaks.get(row.provider) ?? 0) + 1);
        }
      }

      expiredActive.reset();
      for (const provider of PROVIDERS) {
        expiredActive.set({ provider }, leaks.get(provider) ?? 0);
      }

      return registry.metrics();
    },

    /** Record a completed sweep (per-item errors are counted, not thrown). */
    recordSweep(trigger: SweepTrigger, summary: PruneSummary, durationMs: number): void {
      sweeps.inc({ trigger, outcome: summary.errors.length > 0 ? 'error' : 'ok' });
      lastSweep.set({ trigger }, Date.now() / 1000);
      sweepDuration.observe({ trigger }, durationMs / 1000);
      for (const r of summary.reaped) reaped.inc({ provider: r.provider });
      if (summary.reconciled.length > 0) reconciled.inc(summary.reconciled.length);
      for (const e of summary.errors) sweepErrors.inc({ scope: e.scope });
    },

    /** Record a sweep that threw before producing a summary. */
    recordSweepError(trigger: SweepTrigger, durationMs: number): void {
      sweeps.inc({ trigger, outcome: 'error' });
      lastSweep.set({ trigger }, Date.now() / 1000);
      sweepDuration.observe({ trigger }, durationMs / 1000);
    },
  };
}
