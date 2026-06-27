import { z } from 'zod';
import { AWS_REGION_CITIES, BAKED_CATALOG } from './aws-regions.js';

// Regions catalog: generated at runtime from live provider data and
// cached in memory, replacing the retired curated-JSON + KV publish. A slug maps
// to its display string, cloud provider, and that cloud's native region code.
// `GET /regions` serves the cache; `POST /deploy` validates the slug against it.

export const RegionEntrySchema = z.object({
  display: z.string(),
  provider: z.enum(['aws', 'vultr']),
  region: z.string(),
});
export type RegionEntry = z.infer<typeof RegionEntrySchema>;

export const RegionsCatalogSchema = z.record(z.string(), RegionEntrySchema);
export type RegionsCatalog = z.infer<typeof RegionsCatalogSchema>;

export interface VultrRegion {
  id: string;
  city: string;
  country: string;
}

// Vultr's region list is served unauthenticated and carries friendly metro
// names + ISO country directly, so its entries derive without enrichment.
const VULTR_REGIONS_URL = 'https://api.vultr.com/v2/regions?per_page=500';
const VultrRegionsResponse = z.object({
  regions: z.array(z.object({ id: z.string(), city: z.string(), country: z.string() })),
});

// AWS publishes the Lightsail region list in its public Bulk Pricing index (no
// IAM creds); it gives region *codes* only, enriched via AWS_REGION_CITIES.
const AWS_REGION_INDEX_URL =
  'https://pricing.us-east-1.amazonaws.com/offers/v1.0/aws/AmazonLightsail/current/region_index.json';
const AwsRegionIndexResponse = z.object({
  regions: z.record(z.string(), z.object({ regionCode: z.string() })),
});

// A real AWS region code (e.g. `eu-central-1`); used to drop pseudo-entries
// like `aws-other`/`aws-global` while still surfacing genuine unmapped regions.
const AWS_REGION_CODE = /^[a-z]{2}(-[a-z]+)+-\d+$/;

const DEFAULT_TTL_MS = 5 * 60 * 60 * 1000;

export async function fetchVultrRegions(): Promise<VultrRegion[]> {
  const res = await fetch(VULTR_REGIONS_URL);
  if (!res.ok) {
    throw new Error(`Vultr regions fetch failed: ${res.status} ${await res.text()}`);
  }
  return VultrRegionsResponse.parse(await res.json()).regions;
}

export async function fetchAwsRegionCodes(): Promise<string[]> {
  const res = await fetch(AWS_REGION_INDEX_URL);
  if (!res.ok) {
    throw new Error(`Lightsail region index fetch failed: ${res.status} ${await res.text()}`);
  }
  const { regions } = AwsRegionIndexResponse.parse(await res.json());
  return Object.values(regions).map((r) => r.regionCode);
}

/** Strip diacritics to ASCII (`São Paulo` -> `Sao Paulo`), matching the ASCII-only convention. */
export function asciiFold(value: string): string {
  return value.normalize('NFD').replace(/[\u0300-\u036f]/g, '');
}

/** Kebab-case slug from a city name (`New Jersey` -> `new-jersey`). */
export function slugify(value: string): string {
  return asciiFold(value)
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

/**
 * Build the slug->entry catalog from live provider data. AWS is added first so
 * it wins on a city served by both clouds (decision 1b); Vultr fills the rest.
 * Unmapped but genuine AWS codes are still listed with a raw-code display so
 * nothing is silently dropped. Keys are emitted sorted for a stable response.
 */
export function buildCatalog(vultr: VultrRegion[], awsCodes: string[]): RegionsCatalog {
  const catalog: RegionsCatalog = {};

  for (const code of awsCodes) {
    const meta = AWS_REGION_CITIES[code];
    if (meta) {
      const slug = slugify(meta.city);
      if (!catalog[slug]) {
        catalog[slug] = {
          display: `${meta.country} - ${asciiFold(meta.city)}`,
          provider: 'aws',
          region: code,
        };
      }
    } else if (AWS_REGION_CODE.test(code)) {
      const slug = slugify(code);
      if (!catalog[slug]) catalog[slug] = { display: code, provider: 'aws', region: code };
    }
  }

  for (const r of vultr) {
    const slug = slugify(r.city);
    if (!slug || catalog[slug]) continue; // AWS preferred on overlap
    catalog[slug] = {
      display: `${r.country} - ${asciiFold(r.city)}`,
      provider: 'vultr',
      region: r.id,
    };
  }

  return Object.fromEntries(Object.entries(catalog).sort(([a], [b]) => a.localeCompare(b)));
}

export interface RegionsOptions {
  ttlMs?: number;
  fetchVultr?: () => Promise<VultrRegion[]>;
  fetchAws?: () => Promise<string[]>;
  now?: () => number;
}

export type Regions = ReturnType<typeof createRegions>;

/**
 * Lazy-cached catalog provider. Serves the in-memory cache within the TTL; on a
 * cold-cache fetch failure serves the baked snapshot; on a warm-cache refresh
 * failure keeps the stale catalog rather than going dark.
 */
export function createRegions(opts: RegionsOptions = {}) {
  const ttlMs = opts.ttlMs ?? DEFAULT_TTL_MS;
  const fetchVultr = opts.fetchVultr ?? fetchVultrRegions;
  const fetchAws = opts.fetchAws ?? fetchAwsRegionCodes;
  const now = opts.now ?? Date.now;

  let cache: { catalog: RegionsCatalog; fetchedAt: number } | null = null;

  return {
    async getCatalog(): Promise<RegionsCatalog> {
      const ts = now();
      if (cache && ts - cache.fetchedAt < ttlMs) return cache.catalog;

      try {
        const [vultr, awsCodes] = await Promise.all([fetchVultr(), fetchAws()]);
        cache = { catalog: buildCatalog(vultr, awsCodes), fetchedAt: ts };
        return cache.catalog;
      } catch (err) {
        const message = err instanceof Error ? err.message : String(err);
        if (cache) {
          console.warn(`regions refresh failed; serving stale catalog: ${message}`);
          return cache.catalog;
        }
        console.warn(`regions cold fetch failed; serving baked snapshot: ${message}`);
        return BAKED_CATALOG;
      }
    },
  };
}
