import { describe, expect, it, vi } from 'vitest';
import { asciiFold, buildCatalog, createRegions, slugify, type VultrRegion } from './regions.js';

describe('asciiFold + slugify', () => {
  it('folds diacritics to ASCII', () => {
    expect(asciiFold('São Paulo')).toBe('Sao Paulo');
  });

  it('kebab-cases city names', () => {
    expect(slugify('New Jersey')).toBe('new-jersey');
    expect(slugify('São Paulo')).toBe('sao-paulo');
    expect(slugify('Silicon Valley')).toBe('silicon-valley');
  });
});

describe('buildCatalog', () => {
  const vultr: VultrRegion[] = [
    { id: 'ewr', city: 'New Jersey', country: 'US' },
    { id: 'sgp', city: 'Singapore', country: 'SG' }, // overlaps AWS Singapore
    { id: 'sao', city: 'São Paulo', country: 'BR' },
  ];
  const awsCodes = ['eu-central-1', 'ap-southeast-1', 'xx-fake-1', 'aws-other'];

  it('derives AWS entries from the static map', () => {
    const catalog = buildCatalog(vultr, awsCodes);
    expect(catalog.frankfurt).toEqual({
      display: 'DE - Frankfurt',
      provider: 'aws',
      region: 'eu-central-1',
    });
  });

  it('prefers AWS over Vultr on a city served by both', () => {
    const catalog = buildCatalog(vultr, awsCodes);
    expect(catalog.singapore).toMatchObject({ provider: 'aws', region: 'ap-southeast-1' });
  });

  it('derives Vultr entries with folded display', () => {
    const catalog = buildCatalog(vultr, awsCodes);
    expect(catalog['new-jersey']).toMatchObject({ provider: 'vultr', region: 'ewr' });
    expect(catalog['sao-paulo'].display).toBe('BR - Sao Paulo');
  });

  it('surfaces a genuine unmapped AWS code with a raw-code display', () => {
    const catalog = buildCatalog(vultr, awsCodes);
    expect(catalog['xx-fake-1']).toEqual({
      display: 'xx-fake-1',
      provider: 'aws',
      region: 'xx-fake-1',
    });
  });

  it('drops AWS pseudo-entries like aws-other', () => {
    const catalog = buildCatalog(vultr, awsCodes);
    expect(Object.values(catalog).some((e) => e.region === 'aws-other')).toBe(false);
  });

  it('emits keys sorted', () => {
    const keys = Object.keys(buildCatalog(vultr, awsCodes));
    expect(keys).toEqual([...keys].sort());
  });
});

describe('createRegions caching and fallback', () => {
  it('caches within the TTL (fetchers called once)', async () => {
    const fetchVultr = vi.fn(async () => [{ id: 'ewr', city: 'New Jersey', country: 'US' }]);
    const fetchAws = vi.fn(async () => ['eu-central-1']);
    const regions = createRegions({ ttlMs: 1000, fetchVultr, fetchAws, now: () => 0 });

    await regions.getCatalog();
    await regions.getCatalog();

    expect(fetchVultr).toHaveBeenCalledTimes(1);
    expect(fetchAws).toHaveBeenCalledTimes(1);
  });

  it('serves the baked snapshot on a cold-cache fetch failure', async () => {
    const regions = createRegions({
      fetchVultr: async () => {
        throw new Error('network down');
      },
      fetchAws: async () => [],
      now: () => 0,
    });
    const catalog = await regions.getCatalog();
    // Baked snapshot includes the curated entries.
    expect(catalog.frankfurt).toMatchObject({ provider: 'aws' });
  });

  it('keeps the stale catalog on a warm-cache refresh failure', async () => {
    let calls = 0;
    let clock = 0;
    const regions = createRegions({
      ttlMs: 100,
      fetchVultr: async () => {
        calls += 1;
        if (calls === 1) return [{ id: 'ewr', city: 'New Jersey', country: 'US' }];
        throw new Error('refresh failed');
      },
      fetchAws: async () => [],
      now: () => clock,
    });

    const first = await regions.getCatalog();
    expect(first['new-jersey']).toBeDefined();

    clock = 1000; // past the TTL -> triggers a refresh, which fails
    const second = await regions.getCatalog();
    expect(second['new-jersey']).toBeDefined(); // stale catalog retained
  });
});
