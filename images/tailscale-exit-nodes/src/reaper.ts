import type { CloudCreds } from './config.js';
import type { Db } from './db.js';
import { lightsail } from './lib/lightsail.js';
import {
  DEPLOYMENT_ID_TAG,
  EXIT_NODE_TAG,
  EXPIRES_AT_TAG,
  lightsailTagValue,
  parseTimestamp,
  planPrune,
  vultrTagValue,
  type LedgerRow,
  type LiveInstance,
  type Provider,
  type PrunePlan,
  type ReconcileAction,
  type SkipAction,
} from './lib/reaper-core.js';
import { vultr } from './lib/vultr.js';

// Reaper I/O: read the ledger, list instances on both clouds, run the pure
// planner (reaper-core) to decide what to remove, and apply that plan. Runs on a
// timer and also on demand via POST /prune.

const REAPER_INTERVAL_MS = 5 * 60 * 1000;

export interface PruneError {
  scope: string;
  provider?: Provider;
  ref?: string;
  id?: string;
  message: string;
}

export interface PruneSummary {
  swept: { aws: number; vultr: number };
  reaped: Array<{ provider: Provider; ref: string; deploymentId: string | null; reason: string }>;
  reconciled: ReconcileAction[];
  skipped: SkipAction[];
  errors: PruneError[];
}

export interface ReaperDeps {
  creds: CloudCreds;
  db: Db;
}

function errorMessage(err: unknown): string {
  return err instanceof Error ? err.message : String(err);
}

function lightsailClient(creds: CloudCreds) {
  return lightsail(creds.aws);
}

/**
 * Enumerate live exit-node instances across both clouds. Lightsail is per-region,
 * so we only sweep regions the ledger has ever used (bounded by the physical
 * region count); a failure in one region or one cloud is recorded and does not
 * abort the rest of the sweep.
 */
async function sweepClouds(
  creds: CloudCreds,
  rows: LedgerRow[],
): Promise<{ live: LiveInstance[]; errors: PruneError[] }> {
  const live: LiveInstance[] = [];
  const errors: PruneError[] = [];

  const awsRegions = [
    ...new Set(rows.filter((row) => row.provider === 'aws').map((row) => row.provider_region)),
  ];
  const ls = lightsailClient(creds);
  for (const region of awsRegions) {
    try {
      for (const inst of await ls.getInstances(region)) {
        if (!inst.tags?.some((tag) => tag.key === EXIT_NODE_TAG)) continue;
        live.push({
          provider: 'aws',
          ref: inst.name,
          region,
          deploymentId: lightsailTagValue(inst.tags, DEPLOYMENT_ID_TAG) ?? null,
          expiresAt: parseTimestamp(lightsailTagValue(inst.tags, EXPIRES_AT_TAG)),
          firewallRef: null,
        });
      }
    } catch (err) {
      errors.push({ scope: 'sweep-aws', message: `${region}: ${errorMessage(err)}` });
    }
  }

  try {
    for (const inst of await vultr(creds.vultrApiKey).listInstances(EXIT_NODE_TAG)) {
      if (!inst.tags?.includes(EXIT_NODE_TAG)) continue;
      live.push({
        provider: 'vultr',
        ref: inst.id,
        deploymentId: vultrTagValue(inst.tags, DEPLOYMENT_ID_TAG) ?? null,
        expiresAt: parseTimestamp(vultrTagValue(inst.tags, EXPIRES_AT_TAG)),
        firewallRef: inst.firewall_group_id ?? null,
      });
    }
  } catch (err) {
    errors.push({ scope: 'sweep-vultr', message: errorMessage(err) });
  }

  return { live, errors };
}

/**
 * Apply the plan: delete each instance (and any Vultr firewall group), then mark
 * its ledger row destroyed; reconcile rows whose instance is already gone. Every
 * step is isolated: one failure is recorded and the sweep continues, and a row
 * is only marked destroyed after its instance delete succeeds.
 */
async function executePlan(
  creds: CloudCreds,
  db: Db,
  plan: PrunePlan,
): Promise<{
  reaped: PruneSummary['reaped'];
  reconciled: ReconcileAction[];
  errors: PruneError[];
}> {
  const reaped: PruneSummary['reaped'] = [];
  const reconciled: ReconcileAction[] = [];
  const errors: PruneError[] = [];

  const ls = lightsailClient(creds);
  const vt = vultr(creds.vultrApiKey);

  for (const action of plan.deletes) {
    try {
      if (action.provider === 'aws') {
        await ls.deleteInstance(action.region!, action.ref);
      } else {
        await vt.deleteInstance(action.ref);
        if (action.firewallRef) {
          // Best effort: the instance (the billable resource) is gone; a firewall
          // group that lingers is free and rare, so don't let it block the reap.
          try {
            await vt.deleteFirewallGroup(action.firewallRef);
          } catch (err) {
            errors.push({
              scope: 'delete-firewall',
              provider: 'vultr',
              ref: action.firewallRef,
              message: errorMessage(err),
            });
          }
        }
      }
      if (action.deploymentId) await db.markDestroyed(action.deploymentId);
      reaped.push({
        provider: action.provider,
        ref: action.ref,
        deploymentId: action.deploymentId,
        reason: action.reason,
      });
    } catch (err) {
      errors.push({
        scope: 'delete-instance',
        provider: action.provider,
        ref: action.ref,
        message: errorMessage(err),
      });
    }
  }

  for (const action of plan.reconciles) {
    try {
      await db.markDestroyed(action.id);
      reconciled.push(action);
    } catch (err) {
      errors.push({ scope: 'reconcile', id: action.id, message: errorMessage(err) });
    }
  }

  return { reaped, reconciled, errors };
}

/** Run one full sweep: load the ledger, enumerate the clouds, plan, and apply. */
export async function prune(deps: ReaperDeps, now: number = Date.now()): Promise<PruneSummary> {
  const { creds, db } = deps;
  const rows = await db.loadLedger();
  const { live, errors: sweepErrors } = await sweepClouds(creds, rows);
  const plan = planPrune({ rows, live, now });
  const { reaped, reconciled, errors: execErrors } = await executePlan(creds, db, plan);

  return {
    swept: {
      aws: live.filter((inst) => inst.provider === 'aws').length,
      vultr: live.filter((inst) => inst.provider === 'vultr').length,
    },
    reaped,
    reconciled,
    skipped: plan.skips,
    errors: [...sweepErrors, ...execErrors],
  };
}

/**
 * Schedule `sweep`: run it immediately (a fresh process reconciles VMs left by a
 * previous one), then every `intervalMs`. Errors are isolated and logged so one
 * failed sweep never stops the schedule. The sweep itself is injected so the
 * periodic timer and the on-demand /prune route share one metrics-recording
 * path. Returns a stop function that clears the interval.
 */
export function startReaper(
  sweep: () => Promise<unknown>,
  intervalMs: number = REAPER_INTERVAL_MS,
): () => void {
  const tick = async () => {
    try {
      await sweep();
    } catch (err) {
      console.error(`reaper sweep failed: ${errorMessage(err)}`);
    }
  };

  void tick();
  const timer = setInterval(() => void tick(), intervalMs);
  // Don't let the sweep timer keep the process alive on its own.
  if (typeof timer.unref === 'function') timer.unref();
  return () => clearInterval(timer);
}
