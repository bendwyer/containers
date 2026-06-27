import { describe, expect, it } from 'vitest';
import {
  DEPLOYMENT_ID_TAG,
  EXIT_NODE_TAG,
  EXPIRES_AT_TAG,
  lightsailExitNodeTags,
  planPrune,
  vultrExitNodeTags,
  type LedgerRow,
  type LiveInstance,
} from './reaper-core.js';

const NOW = 1_000_000_000_000;
const HOUR = 3_600_000;

function row(overrides: Partial<LedgerRow>): LedgerRow {
  return {
    id: 'd1',
    provider: 'aws',
    provider_region: 'eu-central-1',
    status: 'active',
    instance_ref: 'node-d1',
    firewall_ref: null,
    created_at: NOW - HOUR,
    expires_at: NOW + HOUR,
    ...overrides,
  };
}

function awsLive(overrides: Partial<LiveInstance>): LiveInstance {
  return {
    provider: 'aws',
    ref: 'node-d1',
    region: 'eu-central-1',
    deploymentId: 'd1',
    expiresAt: NOW + HOUR,
    firewallRef: null,
    ...overrides,
  };
}

describe('tag schema', () => {
  it('round-trips Lightsail tags', () => {
    const tags = lightsailExitNodeTags({ deploymentId: 'd1', expiresAt: NOW });
    expect(tags).toEqual([
      { key: EXIT_NODE_TAG },
      { key: DEPLOYMENT_ID_TAG, value: 'd1' },
      { key: EXPIRES_AT_TAG, value: String(NOW) },
    ]);
  });

  it('round-trips Vultr tags', () => {
    expect(vultrExitNodeTags({ deploymentId: 'd1', expiresAt: NOW })).toEqual([
      EXIT_NODE_TAG,
      `${DEPLOYMENT_ID_TAG}:d1`,
      `${EXPIRES_AT_TAG}:${NOW}`,
    ]);
  });
});

describe('planPrune — tracked instances', () => {
  it('reaps an active instance past expires_at, using the ledger not the tag', () => {
    const rows = [row({ expires_at: NOW - HOUR })];
    // Tag still says the future (DELETE clamps the row, not the tag); the ledger wins.
    const live = [awsLive({ expiresAt: NOW + HOUR })];
    const plan = planPrune({ rows, live, now: NOW });
    expect(plan.deletes).toHaveLength(1);
    expect(plan.deletes[0]).toMatchObject({ ref: 'node-d1', deploymentId: 'd1' });
    expect(plan.reconciles).toHaveLength(0);
  });

  it('leaves an active instance that has not expired', () => {
    const plan = planPrune({ rows: [row({})], live: [awsLive({})], now: NOW });
    expect(plan.deletes).toHaveLength(0);
    expect(plan.skips).toHaveLength(1);
  });

  it('reaps an instance from a failed deployment regardless of expiry', () => {
    const plan = planPrune({
      rows: [row({ status: 'failed', expires_at: NOW + 10 * HOUR })],
      live: [awsLive({})],
      now: NOW,
    });
    expect(plan.deletes).toHaveLength(1);
  });

  it('leaves a fresh provisioning instance but reaps a stale one', () => {
    const fresh = planPrune({
      rows: [row({ status: 'provisioning', created_at: NOW - 60_000 })],
      live: [awsLive({})],
      now: NOW,
    });
    expect(fresh.deletes).toHaveLength(0);

    const stale = planPrune({
      rows: [row({ status: 'provisioning', created_at: NOW - 2 * HOUR })],
      live: [awsLive({})],
      now: NOW,
    });
    expect(stale.deletes).toHaveLength(1);
  });

  it('matches by instance_ref when the deployment-id tag is missing', () => {
    const rows = [row({ id: 'd1', instance_ref: 'node-d1', expires_at: NOW - HOUR })];
    // No deployment-id tag, but the ref matches the row; the ledger should drive
    // the decision (expired -> reap) and the row must not also be reconciled.
    const live = [awsLive({ deploymentId: null, ref: 'node-d1', expiresAt: NOW + HOUR })];
    const plan = planPrune({ rows, live, now: NOW });
    expect(plan.deletes).toHaveLength(1);
    expect(plan.deletes[0].deploymentId).toBe('d1');
    expect(plan.reconciles).toHaveLength(0);
  });

  it('reaps a lingering instance whose row is already destroyed', () => {
    const plan = planPrune({
      rows: [row({ status: 'destroyed' })],
      live: [awsLive({})],
      now: NOW,
    });
    expect(plan.deletes).toHaveLength(1);
  });
});

describe('planPrune — orphans (no matching row)', () => {
  it('reaps an orphan past its expires-at tag', () => {
    const plan = planPrune({
      rows: [],
      live: [awsLive({ deploymentId: 'gone', expiresAt: NOW - HOUR })],
      now: NOW,
    });
    expect(plan.deletes).toHaveLength(1);
    expect(plan.deletes[0].deploymentId).toBeNull();
  });

  it('leaves an orphan not yet past its tag', () => {
    const plan = planPrune({
      rows: [],
      live: [awsLive({ deploymentId: 'gone', expiresAt: NOW + HOUR })],
      now: NOW,
    });
    expect(plan.deletes).toHaveLength(0);
    expect(plan.skips).toHaveLength(1);
  });

  it('never touches an instance with no expires-at tag', () => {
    const plan = planPrune({
      rows: [],
      live: [awsLive({ deploymentId: null, expiresAt: null })],
      now: NOW,
    });
    expect(plan.deletes).toHaveLength(0);
    expect(plan.skips).toHaveLength(1);
  });
});

describe('planPrune — reverse reconciliation', () => {
  it('reconciles an active row whose instance is gone', () => {
    const plan = planPrune({ rows: [row({})], live: [], now: NOW });
    expect(plan.deletes).toHaveLength(0);
    expect(plan.reconciles).toEqual([{ id: 'd1', reason: 'active row but instance is gone' }]);
  });

  it('reconciles a failed row with no instance', () => {
    const plan = planPrune({ rows: [row({ status: 'failed' })], live: [], now: NOW });
    expect(plan.reconciles).toHaveLength(1);
  });

  it('does not reconcile a fresh provisioning row with no instance', () => {
    const plan = planPrune({
      rows: [row({ status: 'provisioning', created_at: NOW - 60_000 })],
      live: [],
      now: NOW,
    });
    expect(plan.reconciles).toHaveLength(0);
  });

  it('does not reconcile an already-destroyed row', () => {
    const plan = planPrune({ rows: [row({ status: 'destroyed' })], live: [], now: NOW });
    expect(plan.reconciles).toHaveLength(0);
  });

  it('carries the Vultr firewall group onto the delete action', () => {
    const plan = planPrune({
      rows: [row({ provider: 'vultr', provider_region: 'fra', instance_ref: 'inst1' })],
      live: [
        {
          provider: 'vultr',
          ref: 'inst1',
          deploymentId: 'd1',
          expiresAt: NOW - HOUR,
          firewallRef: 'fw1',
        },
      ],
      now: NOW + 2 * HOUR,
    });
    expect(plan.deletes[0]).toMatchObject({ provider: 'vultr', ref: 'inst1', firewallRef: 'fw1' });
  });
});
