import { describe, expect, it } from 'vitest';
import type { Db, MetricsSnapshot } from './db.js';
import type { PruneSummary } from './reaper.js';
import { createMetrics } from './metrics.js';

// A Db whose only method the metrics module touches is metricsSnapshot().
function fakeDb(snapshot: MetricsSnapshot): Db {
  return {
    async metricsSnapshot(): Promise<MetricsSnapshot> {
      return snapshot;
    },
  } as unknown as Db;
}

// Find the exposition line for `name` that contains every fragment in `has`.
// Order-independent so it doesn't depend on prom-client's label ordering.
function line(out: string, name: string, has: string[] = []): string | undefined {
  return out
    .split('\n')
    .find((l) => l.startsWith(name) && has.every((fragment) => l.includes(fragment)));
}

describe('metrics render (ledger gauges)', () => {
  it('emits deployment counts, live expiry, and zeroed leak series', async () => {
    const now = Date.now();
    const metrics = createMetrics(
      fakeDb({
        counts: [
          { status: 'active', provider: 'aws', region: 'frankfurt', n: 2 },
          { status: 'destroyed', provider: 'vultr', region: 'new-jersey', n: 5 },
        ],
        live: [
          {
            id: 'a1',
            name: 'travel',
            provider: 'aws',
            region: 'frankfurt',
            status: 'active',
            expiresAt: now + 3_600_000,
          },
        ],
      }),
    );

    const out = await metrics.render();

    expect(
      line(out, 'exit_node_deployments', [
        'status="active"',
        'provider="aws"',
        'region="frankfurt"',
      ]),
    ).toMatch(/ 2$/);
    expect(line(out, 'exit_node_expires_at_seconds', ['id="a1"', 'name="travel"'])).toMatch(
      / \d+$/,
    );
    // The leak series exists (and is 0) for every provider so it can be alerted on.
    expect(line(out, 'exit_node_expired_active', ['provider="aws"'])).toMatch(/ 0$/);
    expect(line(out, 'exit_node_expired_active', ['provider="vultr"'])).toMatch(/ 0$/);
  });

  it('flags an active deployment past its expiry as leaked', async () => {
    const now = Date.now();
    const metrics = createMetrics(
      fakeDb({
        counts: [],
        live: [
          {
            id: 'x',
            name: 'stuck',
            provider: 'vultr',
            region: 'nj',
            status: 'active',
            expiresAt: now - 60_000,
          },
        ],
      }),
    );

    const out = await metrics.render();
    expect(line(out, 'exit_node_expired_active', ['provider="vultr"'])).toMatch(/ 1$/);
  });

  it('rebuilds gauges each scrape so a terminated node drops out', async () => {
    let snapshot: MetricsSnapshot = {
      counts: [],
      live: [
        {
          id: 'g',
          name: 'gone',
          provider: 'aws',
          region: 'fra',
          status: 'active',
          expiresAt: Date.now() + 1_000,
        },
      ],
    };
    const db = {
      async metricsSnapshot() {
        return snapshot;
      },
    } as unknown as Db;
    const metrics = createMetrics(db);

    expect(await metrics.render()).toContain('exit_node_expires_at_seconds{id="g"');
    snapshot = { counts: [], live: [] };
    expect(await metrics.render()).not.toContain('exit_node_expires_at_seconds{id="g"');
  });
});

describe('metrics recordSweep (reaper counters)', () => {
  const summary: PruneSummary = {
    swept: { aws: 1, vultr: 0 },
    reaped: [{ provider: 'aws', ref: 'i-1', deploymentId: 'd1', reason: 'expired' }],
    reconciled: [{ id: 'd2', reason: 'gone' }],
    skipped: [],
    errors: [{ scope: 'sweep-vultr', message: 'timeout' }],
  };

  it('counts sweeps, reaps, reconciles, and per-scope errors', async () => {
    const metrics = createMetrics(fakeDb({ counts: [], live: [] }));
    metrics.recordSweep('periodic', summary, 1500);
    const out = await metrics.render();

    expect(
      line(out, 'exit_node_reaper_sweeps_total', ['trigger="periodic"', 'outcome="error"']),
    ).toMatch(/ 1$/);
    expect(line(out, 'exit_node_reaper_reaped_total', ['provider="aws"'])).toMatch(/ 1$/);
    expect(line(out, 'exit_node_reaper_reconciled_total')).toMatch(/ 1$/);
    expect(line(out, 'exit_node_reaper_errors_total', ['scope="sweep-vultr"'])).toMatch(/ 1$/);
    expect(
      line(out, 'exit_node_reaper_last_sweep_timestamp_seconds', ['trigger="periodic"']),
    ).toMatch(/ \d/);
  });

  it('recordSweepError marks an errored outcome without a summary', async () => {
    const metrics = createMetrics(fakeDb({ counts: [], live: [] }));
    metrics.recordSweepError('manual', 200);
    const out = await metrics.render();
    expect(
      line(out, 'exit_node_reaper_sweeps_total', ['trigger="manual"', 'outcome="error"']),
    ).toMatch(/ 1$/);
  });
});
