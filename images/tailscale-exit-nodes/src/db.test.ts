import { newDb } from 'pg-mem';
import type { Pool } from 'pg';
import { beforeEach, describe, expect, it } from 'vitest';
import { createDb, type Db, type NewDeployment } from './db.js';

function newDeployment(overrides: Partial<NewDeployment> = {}): NewDeployment {
  return {
    id: 'd1',
    name: 'travel',
    regionSlug: 'frankfurt',
    provider: 'aws',
    providerRegion: 'eu-central-1',
    hostname: 'travel-d1abcdef',
    createdAt: 1_000_000_000_000,
    expiresAt: 1_000_000_000_000 + 3_600_000,
    ...overrides,
  };
}

let db: Db;

beforeEach(async () => {
  const mem = newDb();
  const pg = mem.adapters.createPg();
  const pool = new pg.Pool() as unknown as Pool;
  db = createDb(pool);
  await db.migrate();
});

describe('db', () => {
  it('inserts and lists a deployment with numeric timestamps', async () => {
    await db.insertDeployment(newDeployment());
    const list = await db.listDeployments();
    expect(list).toHaveLength(1);
    expect(list[0]).toMatchObject({ id: 'd1', status: 'provisioning', provider: 'aws' });
    expect(typeof list[0].created_at).toBe('number');
    expect(typeof list[0].expires_at).toBe('number');
  });

  it('reconcileStartup marks provisioning rows failed', async () => {
    await db.insertDeployment(newDeployment({ id: 'a' }));
    await db.insertDeployment(newDeployment({ id: 'b' }));
    const count = await db.reconcileStartup();
    expect(count).toBe(2);
    const list = await db.listDeployments();
    expect(list.every((d) => d.status === 'failed')).toBe(true);
  });

  it('records refs, active, failed, and destroyed transitions', async () => {
    await db.insertDeployment(newDeployment());

    await db.updateInstanceRefs('d1', 'inst-1', 'fwg-1');
    await db.markActive('d1', '203.0.113.5');
    const ledger = await db.loadLedger();
    expect(ledger[0]).toMatchObject({
      status: 'active',
      instance_ref: 'inst-1',
      firewall_ref: 'fwg-1',
    });

    await db.markFailed('d1', 'boom');
    expect((await db.listDeployments())[0].error).toBe('boom');

    await db.markDestroyed('d1');
    expect((await db.loadLedger())[0].status).toBe('destroyed');
  });

  it('clampExpiry lowers a future expiry and reports the change', async () => {
    await db.insertDeployment(newDeployment());
    const now = 1_000_000_000_000 + 60_000;
    const changed = await db.clampExpiry('d1', now);
    expect(changed).toBe(1);
    expect((await db.loadLedger())[0].expires_at).toBe(now);
  });

  it('clampExpiry never raises a sooner expiry', async () => {
    await db.insertDeployment(newDeployment({ expiresAt: 1_000_000_000_000 + 10_000 }));
    const changed = await db.clampExpiry('d1', 1_000_000_000_000 + 999_999);
    expect(changed).toBe(1);
    expect((await db.loadLedger())[0].expires_at).toBe(1_000_000_000_000 + 10_000);
  });

  it('clampExpiry returns 0 for an unknown id', async () => {
    expect(await db.clampExpiry('nope', Date.now())).toBe(0);
  });

  it('loadLedger returns numeric timestamps', async () => {
    await db.insertDeployment(newDeployment());
    const ledger = await db.loadLedger();
    expect(typeof ledger[0].created_at).toBe('number');
    expect(typeof ledger[0].expires_at).toBe('number');
  });

  it('ping resolves against a live connection', async () => {
    await expect(db.ping()).resolves.toBeUndefined();
  });

  it('metricsSnapshot groups all rows and returns only live ones', async () => {
    await db.insertDeployment(newDeployment({ id: 'a' })); // provisioning
    await db.insertDeployment(newDeployment({ id: 'b' }));
    await db.markActive('b', '203.0.113.9'); // active
    await db.insertDeployment(newDeployment({ id: 'c' }));
    await db.markDestroyed('c'); // terminal

    const snap = await db.metricsSnapshot();

    expect(snap.counts.reduce((n, c) => n + c.n, 0)).toBe(3);
    expect(snap.counts.map((c) => c.status)).toContain('destroyed');
    expect(typeof snap.counts[0].n).toBe('number');

    // The destroyed row is excluded from the live set.
    expect(snap.live.map((r) => r.id).sort()).toEqual(['a', 'b']);
    expect(typeof snap.live[0].expiresAt).toBe('number');
  });
});
