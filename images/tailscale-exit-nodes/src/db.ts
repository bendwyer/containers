import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import type { Pool } from 'pg';
import type { LedgerRow, Provider } from './lib/reaper-core.js';

// Data layer over the Postgres `deployments` ledger. The Pool is injected so the
// tests can back it with pg-mem and the runtime with a real connection. SQL
// notes: BIGINT times, `$n` placeholders, `rowCount` for change detection, and a
// CASE clamp for the expiry-clamp update (pg-mem-friendly).

// One migration file, applied idempotently on boot (CREATE ... IF NOT EXISTS).
const MIGRATION_PATH = new URL('../migrations/0001_deployments.sql', import.meta.url);

/** A full deployments row, as returned to the UI by GET /list. */
export interface Deployment {
  id: string;
  name: string;
  region_slug: string;
  provider: Provider;
  provider_region: string;
  hostname: string;
  status: string;
  instance_ref: string | null;
  firewall_ref: string | null;
  public_ip: string | null;
  error: string | null;
  created_at: number;
  expires_at: number;
}

/** A grouped count of deployments, for the exit_node_deployments gauge. */
export interface MetricsCount {
  status: string;
  provider: Provider;
  region: string;
  n: number;
}

/** A live (provisioning/active) deployment, for the per-node expiry gauge. */
export interface MetricsLiveRow {
  id: string;
  name: string;
  provider: Provider;
  region: string;
  status: string;
  expiresAt: number;
}

/** The bounded-cardinality view GET /metrics builds its gauges from. */
export interface MetricsSnapshot {
  counts: MetricsCount[];
  live: MetricsLiveRow[];
}

/** The fields POST /deploy supplies when inserting a fresh `provisioning` row. */
export interface NewDeployment {
  id: string;
  name: string;
  regionSlug: string;
  provider: Provider;
  providerRegion: string;
  hostname: string;
  createdAt: number;
  expiresAt: number;
}

// BIGINT columns arrive from node-pg as strings; the planner and the UI both
// want numbers. Coerce on the way out (values are unix-ms, far below 2^53).
function toNumber(value: unknown): number {
  return typeof value === 'number' ? value : Number(value);
}

export type Db = ReturnType<typeof createDb>;

export function createDb(pool: Pool) {
  return {
    /** Cheap liveness check for the DB connection (readiness probe). */
    async ping(): Promise<void> {
      await pool.query('SELECT 1');
    },

    /** Apply the schema (idempotent). Run once on boot before serving. */
    async migrate(): Promise<void> {
      const sql = readFileSync(fileURLToPath(MIGRATION_PATH), 'utf8');
      await pool.query(sql);
    },

    /**
     * Mark any row left `provisioning` by a previous process `failed` — the saga
     * that owned it died when the process restarted, so its VM (if any) is reaped
     * by the reaper via tags. Run once on boot, after migrate.
     */
    async reconcileStartup(): Promise<number> {
      const result = await pool.query(
        `UPDATE deployments SET status = 'failed', error = $1 WHERE status = 'provisioning'`,
        ['control-plane restarted during provisioning'],
      );
      return result.rowCount ?? 0;
    },

    async insertDeployment(d: NewDeployment): Promise<void> {
      await pool.query(
        `INSERT INTO deployments
           (id, name, region_slug, provider, provider_region, hostname, status, created_at, expires_at)
         VALUES ($1, $2, $3, $4, $5, $6, 'provisioning', $7, $8)`,
        [
          d.id,
          d.name,
          d.regionSlug,
          d.provider,
          d.providerRegion,
          d.hostname,
          d.createdAt,
          d.expiresAt,
        ],
      );
    },

    async listDeployments(): Promise<Deployment[]> {
      const { rows } = await pool.query(
        `SELECT id, name, region_slug, provider, provider_region, hostname, status,
                instance_ref, firewall_ref, public_ip, error, created_at, expires_at
           FROM deployments
           ORDER BY created_at DESC`,
      );
      return rows.map((r) => ({
        ...r,
        created_at: toNumber(r.created_at),
        expires_at: toNumber(r.expires_at),
      })) as Deployment[];
    },

    /**
     * Clamp a deployment's expiry down to `now` so the reaper tears it down on
     * its next sweep. Clamp (never raise) so a sooner natural expiry still wins.
     * Returns the number of rows changed (0 = not found).
     */
    async clampExpiry(id: string, now: number): Promise<number> {
      const result = await pool.query(
        `UPDATE deployments
            SET expires_at = CASE WHEN expires_at > $2 THEN $2 ELSE expires_at END
          WHERE id = $1`,
        [id, now],
      );
      return result.rowCount ?? 0;
    },

    /** Persist the cloud handles as soon as the instance exists. */
    async updateInstanceRefs(
      id: string,
      instanceRef: string,
      firewallRef: string | null,
    ): Promise<void> {
      await pool.query(
        `UPDATE deployments SET instance_ref = $2, firewall_ref = $3 WHERE id = $1`,
        [id, instanceRef, firewallRef],
      );
    },

    async markActive(id: string, publicIp: string): Promise<void> {
      await pool.query(`UPDATE deployments SET status = 'active', public_ip = $2 WHERE id = $1`, [
        id,
        publicIp,
      ]);
    },

    async markFailed(id: string, error: string): Promise<void> {
      await pool.query(`UPDATE deployments SET status = 'failed', error = $2 WHERE id = $1`, [
        id,
        error,
      ]);
    },

    async markDestroyed(id: string): Promise<void> {
      await pool.query(`UPDATE deployments SET status = 'destroyed' WHERE id = $1`, [id]);
    },

    /**
     * A bounded snapshot for the metrics endpoint: grouped counts across the
     * whole ledger, plus the live (provisioning/active) rows only. Two cheap
     * grouped reads rather than pulling the full ledger on every scrape, and
     * per-node series stay bounded to what is actually live.
     */
    async metricsSnapshot(): Promise<MetricsSnapshot> {
      const counts = await pool.query(
        `SELECT status, provider, region_slug AS region, count(*) AS n
           FROM deployments
           GROUP BY status, provider, region_slug`,
      );
      const live = await pool.query(
        `SELECT id, name, provider, region_slug AS region, status, expires_at
           FROM deployments
           WHERE status IN ('provisioning', 'active')`,
      );
      return {
        counts: counts.rows.map((r) => ({
          status: r.status,
          provider: r.provider as Provider,
          region: r.region,
          n: toNumber(r.n),
        })),
        live: live.rows.map((r) => ({
          id: r.id,
          name: r.name,
          provider: r.provider as Provider,
          region: r.region,
          status: r.status,
          expiresAt: toNumber(r.expires_at),
        })),
      };
    },

    /** Load the ledger columns the reaper sweep reasons about. */
    async loadLedger(): Promise<LedgerRow[]> {
      const { rows } = await pool.query(
        `SELECT id, provider, provider_region, status, instance_ref, firewall_ref, created_at, expires_at
           FROM deployments`,
      );
      return rows.map((r) => ({
        id: r.id,
        provider: r.provider as Provider,
        provider_region: r.provider_region,
        status: r.status,
        instance_ref: r.instance_ref,
        firewall_ref: r.firewall_ref,
        created_at: toNumber(r.created_at),
        expires_at: toNumber(r.expires_at),
      }));
    },
  };
}
