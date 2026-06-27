import { newDb } from 'pg-mem';
import type { Pool } from 'pg';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CloudCreds } from './config.js';
import { createDb, type Db } from './db.js';
import { provisionSaga, type ProvisionParams } from './saga.js';

const CREDS: CloudCreds = {
  aws: { accessKeyId: 'AKIAEXAMPLE', secretAccessKey: 'secretEXAMPLE' },
  vultrApiKey: 'VULTREXAMPLE',
  tailscale: { clientId: 'tskey-client', clientSecret: 'tskey-secret' },
};

const AWS_PARAMS: ProvisionParams = {
  deploymentId: 'd1',
  provider: 'aws',
  providerRegion: 'eu-central-1',
  name: 'travel',
  hostname: 'travel-d1abcdef',
  expiresAt: 1_000_000_000_000 + 3_600_000,
};

// Fast, deterministic saga: a few attempts, no real waiting.
const FAST = { pollAttempts: 3, pollIntervalMs: 0, sleep: async () => {} };

let db: Db;

beforeEach(async () => {
  const mem = newDb();
  const pg = mem.adapters.createPg();
  const pool = new pg.Pool() as unknown as Pool;
  db = createDb(pool);
  await db.migrate();
  await db.insertDeployment({
    id: 'd1',
    name: 'travel',
    regionSlug: 'frankfurt',
    provider: 'aws',
    providerRegion: 'eu-central-1',
    hostname: 'travel-d1abcdef',
    createdAt: 1_000_000_000_000,
    expiresAt: AWS_PARAMS.expiresAt,
  });
});

afterEach(() => vi.restoreAllMocks());

describe('provisionSaga (AWS happy path)', () => {
  it('mints a key, creates the instance, and marks it active with its IP', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const req = input instanceof Request ? input : new Request(input, init);
      const url = new URL(req.url);
      const target = req.headers.get('x-amz-target') ?? '';

      if (url.host === 'api.tailscale.com') {
        return url.pathname.endsWith('/oauth/token')
          ? Response.json({ access_token: 'tok' })
          : Response.json({ id: 'k', key: 'tskey-auth-MINTED' });
      }
      if (url.host.startsWith('lightsail.')) {
        if (target.endsWith('CreateInstances')) return Response.json({ operations: [] });
        if (target.endsWith('PutInstancePublicPorts')) return Response.json({ operation: {} });
        if (target.endsWith('GetInstance')) {
          return Response.json({
            instance: {
              name: 'travel-d1abcdef',
              state: { code: 16, name: 'running' },
              publicIpAddress: '203.0.113.9',
            },
          });
        }
      }
      throw new Error(`unexpected fetch ${req.method} ${req.url} (${target})`);
    });

    await provisionSaga({ creds: CREDS, db }, AWS_PARAMS, FAST);

    const ledger = await db.loadLedger();
    expect(ledger[0]).toMatchObject({ status: 'active', instance_ref: 'travel-d1abcdef' });
    expect((await db.listDeployments())[0].public_ip).toBe('203.0.113.9');
  });
});

describe('provisionSaga (failure)', () => {
  it('records the row failed when key minting fails', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      return new Response('invalid_client', { status: 401 });
    });

    await provisionSaga({ creds: CREDS, db }, AWS_PARAMS, FAST);

    const list = await db.listDeployments();
    expect(list[0].status).toBe('failed');
    expect(list[0].error).toMatch(/Tailscale OAuth token request failed/);
  });

  it('does not throw to the caller (runs detached)', async () => {
    vi.spyOn(globalThis, 'fetch').mockImplementation(async () => {
      throw new Error('network gone');
    });

    await expect(provisionSaga({ creds: CREDS, db }, AWS_PARAMS, FAST)).resolves.toBeUndefined();
    expect((await db.listDeployments())[0].status).toBe('failed');
  });
});
