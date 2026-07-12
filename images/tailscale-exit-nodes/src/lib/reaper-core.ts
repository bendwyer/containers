import { type LightsailTag } from './lightsail.js';

// Reaper core: the PURE tag-reconciliation planner shared by the reaper sweep
// and the on-demand POST /prune route. It reconciles the cloud providers
// against the deployments ledger and decides which instances should no longer
// exist. All policy lives here; the I/O that reads the ledger, calls the cloud
// clients, and applies the plan lives in `src/reaper.ts`. Keeping the planner
// pure makes every branch exhaustively unit-testable without any runtime.

// --- Tag schema ------------------------------------------------------------
//
// Every exit-node instance is tagged at create time so a sweep can be matched
// back to the ledger and reaped even when ledger state is incomplete:
//   exit-node            marks the instance as ours; the Reaper ONLY ever
//                        deletes instances carrying this tag.
//   deployment-id:<uuid> links the instance to its ledger row even if the row's
//                        instance_ref was never recorded (a saga that created
//                        the VM but died before record-refs).
//   expires-at:<unix-ms> the instance's own copy of its create-time TTL, so an
//                        orphan with no ledger row at all is still self-describing
//                        and reapable. (The ledger's expires_at is authoritative
//                        for tracked rows; DELETE clamps the row, not the tag.)
//
// Lightsail tags are native key/value pairs; Vultr tags are a flat string array,
// so the keyed tags are encoded as "key:value" strings there.

export const EXIT_NODE_TAG = 'exit-node';
export const DEPLOYMENT_ID_TAG = 'deployment-id';
export const EXPIRES_AT_TAG = 'expires-at';

export interface ExitNodeTagInput {
  deploymentId: string;
  /** Unix epoch ms; the instance's create-time TTL. */
  expiresAt: number;
}

export function lightsailExitNodeTags(input: ExitNodeTagInput): LightsailTag[] {
  return [
    { key: EXIT_NODE_TAG },
    { key: DEPLOYMENT_ID_TAG, value: input.deploymentId },
    { key: EXPIRES_AT_TAG, value: String(input.expiresAt) },
  ];
}

export function vultrExitNodeTags(input: ExitNodeTagInput): string[] {
  return [
    EXIT_NODE_TAG,
    `${DEPLOYMENT_ID_TAG}:${input.deploymentId}`,
    `${EXPIRES_AT_TAG}:${input.expiresAt}`,
  ];
}

export function lightsailTagValue(
  tags: LightsailTag[] | undefined,
  key: string,
): string | undefined {
  return tags?.find((tag) => tag.key === key)?.value;
}

export function vultrTagValue(tags: string[] | undefined, key: string): string | undefined {
  const prefix = `${key}:`;
  return tags?.find((tag) => tag.startsWith(prefix))?.slice(prefix.length);
}

export function parseTimestamp(value: string | undefined): number | null {
  if (value === undefined) return null;
  const n = Number(value);
  return Number.isFinite(n) ? n : null;
}

// Stuck-saga threshold: a `provisioning` row this old is presumed dead (the
// saga ceiling is ~5 min including retries), so its instance is reaped and its
// row reconciled. Far above any real saga so an in-flight deploy is never torn
// down mid-provision.
export const STALE_PROVISIONING_MS = 60 * 60 * 1000;

// --- Data shapes -----------------------------------------------------------

export type Provider = 'aws' | 'vultr';

/** A deployments-ledger row, narrowed to the columns the sweep reasons about. */
export interface LedgerRow {
  id: string;
  provider: Provider;
  provider_region: string;
  status: string;
  instance_ref: string | null;
  firewall_ref: string | null;
  created_at: number;
  expires_at: number;
}

/** A live cloud instance, normalized across providers. */
export interface LiveInstance {
  provider: Provider;
  /** Cloud delete handle: Lightsail instance name, or Vultr instance id. */
  ref: string;
  /** AWS region (required to delete a Lightsail instance); undefined for Vultr. */
  region?: string;
  /** From the deployment-id tag; null if the instance predates tagging. */
  deploymentId: string | null;
  /** From the expires-at tag; null if absent or unparseable. */
  expiresAt: number | null;
  /** Vultr firewall group to delete alongside the instance; null for Lightsail. */
  firewallRef: string | null;
}

export interface DeleteAction {
  provider: Provider;
  ref: string;
  region?: string;
  firewallRef: string | null;
  /** Ledger row to mark destroyed after the delete; null for an untracked orphan. */
  deploymentId: string | null;
  reason: string;
}

export interface ReconcileAction {
  id: string;
  reason: string;
}

export interface SkipAction {
  provider: Provider;
  ref: string;
  reason: string;
}

export interface PrunePlan {
  deletes: DeleteAction[];
  reconciles: ReconcileAction[];
  skips: SkipAction[];
}

export interface PlanInput {
  rows: LedgerRow[];
  live: LiveInstance[];
  now: number;
  staleProvisioningMs?: number;
}

// --- The planner (pure) ----------------------------------------------------

/**
 * Decide, from ledger rows + live instances, which instances to delete, which
 * rows to reconcile (their instance is already gone), and which to leave alone.
 * No I/O; every branch is unit-testable.
 */
export function planPrune({
  rows,
  live,
  now,
  staleProvisioningMs = STALE_PROVISIONING_MS,
}: PlanInput): PrunePlan {
  const rowsById = new Map(rows.map((row) => [row.id, row]));
  // Fallback index for matching by the cloud handle when an instance is missing
  // its deployment-id tag but its row recorded an instance_ref.
  const rowsByRef = new Map(
    rows
      .filter((row): row is LedgerRow & { instance_ref: string } => row.instance_ref !== null)
      .map((row) => [`${row.provider}:${row.instance_ref}`, row]),
  );
  // Row ids whose instance was found live this sweep; the rest of the ledger's
  // rows are reconciled below.
  const seen = new Set<string>();

  const deletes: DeleteAction[] = [];
  const skips: SkipAction[] = [];

  for (const inst of live) {
    const row =
      (inst.deploymentId ? rowsById.get(inst.deploymentId) : undefined) ??
      rowsByRef.get(`${inst.provider}:${inst.ref}`);
    if (row) seen.add(row.id);

    const decision = decideLive(inst, row, now, staleProvisioningMs);
    if (decision.reap) {
      deletes.push({
        provider: inst.provider,
        ref: inst.ref,
        region: inst.region,
        firewallRef: inst.firewallRef ?? row?.firewall_ref ?? null,
        deploymentId: row?.id ?? null,
        reason: decision.reason,
      });
    } else {
      skips.push({ provider: inst.provider, ref: inst.ref, reason: decision.reason });
    }
  }

  const reconciles: ReconcileAction[] = [];
  for (const row of rows) {
    if (seen.has(row.id)) continue; // instance is live; handled above
    const decision = decideMissing(row, now, staleProvisioningMs);
    if (decision.reconcile) reconciles.push({ id: row.id, reason: decision.reason });
  }

  return { deletes, reconciles, skips };
}

function decideLive(
  inst: LiveInstance,
  row: LedgerRow | undefined,
  now: number,
  staleMs: number,
): { reap: boolean; reason: string } {
  if (!row) {
    // Orphan: no ledger row references this instance. Reap purely from its tag.
    if (inst.expiresAt === null) {
      return { reap: false, reason: 'orphan without an expires-at tag; left untouched' };
    }
    return now >= inst.expiresAt
      ? { reap: true, reason: 'orphan past its expires-at tag' }
      : { reap: false, reason: 'orphan not yet expired' };
  }

  switch (row.status) {
    case 'failed':
      return { reap: true, reason: 'instance from a failed deployment' };
    case 'provisioning':
      return now - row.created_at >= staleMs
        ? { reap: true, reason: 'stuck provisioning past the stale threshold' }
        : { reap: false, reason: 'provisioning in flight' };
    case 'active':
      return now >= row.expires_at
        ? { reap: true, reason: 'active deployment past expires_at' }
        : { reap: false, reason: 'active and not yet expired' };
    default:
      // 'destroyed' (or anything terminal): the row says gone but the VM
      // lingers, a delete that silently didn't take. Reap it.
      return { reap: true, reason: `instance for a '${row.status}' row still present` };
  }
}

function decideMissing(
  row: LedgerRow,
  now: number,
  staleMs: number,
): { reconcile: boolean; reason: string } {
  switch (row.status) {
    case 'active':
      return { reconcile: true, reason: 'active row but instance is gone' };
    case 'failed':
      return { reconcile: true, reason: 'failed row with no instance' };
    case 'provisioning':
      return now - row.created_at >= staleMs
        ? { reconcile: true, reason: 'provisioning row stuck with no instance' }
        : { reconcile: false, reason: 'provisioning in flight' };
    default:
      // 'destroyed' / unknown: already terminal, nothing to do.
      return { reconcile: false, reason: '' };
  }
}
