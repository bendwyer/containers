import { describe, expect, it, vi } from 'vitest';
import type { Db, Deployment, NewDeployment } from './db.js';
import type { Regions, RegionsCatalog } from './regions.js';
import type { PruneSummary } from './reaper.js';
import { createApp, type ServerDeps } from './server.js';

const CATALOG: RegionsCatalog = {
  frankfurt: { display: 'DE - Frankfurt', provider: 'aws', region: 'eu-central-1' },
  'new-jersey': { display: 'US - New Jersey', provider: 'vultr', region: 'ewr' },
};

const EMPTY_SUMMARY: PruneSummary = {
  swept: { aws: 0, vultr: 0 },
  reaped: [],
  reconciled: [],
  skipped: [],
  errors: [],
};

interface Harness {
  app: ReturnType<typeof createApp>;
  inserted: NewDeployment[];
  startSaga: ReturnType<typeof vi.fn>;
  deps: ServerDeps;
}

function harness(overrides: Partial<ServerDeps> = {}): Harness {
  const inserted: NewDeployment[] = [];
  const startSaga = vi.fn();

  const db = {
    async insertDeployment(d: NewDeployment) {
      inserted.push(d);
    },
    async listDeployments(): Promise<Deployment[]> {
      return [];
    },
    async clampExpiry(id: string) {
      return id === 'known' ? 1 : 0;
    },
    async ping() {},
  } as unknown as Db;

  const regions = { getCatalog: async () => CATALOG } as unknown as Regions;

  const deps: ServerDeps = {
    db,
    regions,
    startSaga,
    runPrune: async () => EMPTY_SUMMARY,
    ...overrides,
  };

  return { app: createApp(deps), inserted, startSaga, deps };
}

function postJson(path: string, body: unknown): Request {
  return new Request(`http://local${path}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
}

describe('GET /healthz + /readyz', () => {
  it('healthz is DB-free and always ok', async () => {
    const { app } = harness();
    const res = await app.request('/healthz');
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual({ status: 'ok' });
  });

  it('readyz reports ready when the DB pings', async () => {
    const { app } = harness();
    const res = await app.request('/readyz');
    expect(res.status).toBe(200);
  });

  it('readyz reports 503 when the DB is down', async () => {
    const db = {
      async ping() {
        throw new Error('connection refused');
      },
    } as unknown as Db;
    const { app } = harness({ db });
    const res = await app.request('/readyz');
    expect(res.status).toBe(503);
  });
});

describe('GET /regions', () => {
  it('returns the catalog', async () => {
    const { app } = harness();
    const res = await app.request('/regions');
    expect(await res.json()).toEqual(CATALOG);
  });
});

describe('POST /deploy', () => {
  it('inserts a row, kicks the saga, and returns 202', async () => {
    const { app, inserted, startSaga } = harness();
    const res = await app.request(
      postJson('/deploy', { region: 'frankfurt', name: 'travel', ttl_hours: 8 }),
    );

    expect(res.status).toBe(202);
    const body = (await res.json()) as { status: string; hostname: string };
    expect(body).toMatchObject({ status: 'provisioning' });
    expect(body.hostname).toMatch(/^travel-/);

    expect(inserted).toHaveLength(1);
    expect(inserted[0]).toMatchObject({ provider: 'aws', providerRegion: 'eu-central-1' });
    expect(startSaga).toHaveBeenCalledTimes(1);
    expect(startSaga.mock.calls[0][0]).toMatchObject({ provider: 'aws', name: 'travel' });
  });

  it('rejects an invalid name with 400', async () => {
    const { app } = harness();
    const res = await app.request(
      postJson('/deploy', { region: 'frankfurt', name: 'Bad Name', ttl_hours: 8 }),
    );
    expect(res.status).toBe(400);
  });

  it('rejects a ttl over the cap with 400', async () => {
    const { app } = harness();
    const res = await app.request(
      postJson('/deploy', { region: 'frankfurt', name: 'x', ttl_hours: 99999 }),
    );
    expect(res.status).toBe(400);
  });

  it('404s an unknown region', async () => {
    const { app, startSaga } = harness();
    const res = await app.request(
      postJson('/deploy', { region: 'narnia', name: 'x', ttl_hours: 8 }),
    );
    expect(res.status).toBe(404);
    expect(startSaga).not.toHaveBeenCalled();
  });

  it('400s a non-JSON body', async () => {
    const { app } = harness();
    const res = await app.request(
      new Request('http://local/deploy', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: 'not json',
      }),
    );
    expect(res.status).toBe(400);
  });
});

describe('DELETE /deployments/:id', () => {
  it('202s a known id', async () => {
    const { app } = harness();
    const res = await app.request('/deployments/known', { method: 'DELETE' });
    expect(res.status).toBe(202);
    expect(await res.json()).toMatchObject({ status: 'expiring' });
  });

  it('404s an unknown id', async () => {
    const { app } = harness();
    const res = await app.request('/deployments/missing', { method: 'DELETE' });
    expect(res.status).toBe(404);
  });
});

describe('POST /prune', () => {
  it('returns the sweep summary', async () => {
    const { app } = harness();
    const res = await app.request('/prune', { method: 'POST' });
    expect(res.status).toBe(200);
    expect(await res.json()).toEqual(EMPTY_SUMMARY);
  });
});
