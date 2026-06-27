import type { CloudCreds } from './config.js';
import type { Db } from './db.js';
import { renderCloudInit } from './lib/cloud-init.js';
import { lightsail } from './lib/lightsail.js';
import { lightsailExitNodeTags, vultrExitNodeTags, type Provider } from './lib/reaper-core.js';
import { tailscale } from './lib/tailscale.js';
import { vultr } from './lib/vultr.js';

// Provisioning saga: ported from the retired durable Workflow to a plain async
// function (the CF Workflow `step.do`/`step.sleep` durability is stripped). Each
// step is a standalone, unit-testable function; provisionSaga wires them in
// order and records terminal state on the deployments row. It runs detached from
// POST /deploy (fire-and-forget) — failures land as a `failed` row, not a throw.

// The Tailscale ACL tag every cloud exit node advertises and every minted key
// is scoped to. Distinct from any other exit nodes on the tailnet so the OAuth
// client's blast radius is limited to these cloud-provisioned nodes.
const CLOUD_EXIT_NODE_TAG = 'tag:cloud-exit-node';
// Ephemeral key lifetime — only needs to outlive first boot + `tailscale up`.
const KEY_EXPIRY_SECONDS = 600;
// Readiness polling: up to ~5 minutes for the instance to boot and get an IP.
const READY_POLL_ATTEMPTS = 20;
const READY_POLL_INTERVAL_MS = 15_000;

export interface ProvisionParams {
  // Ledger row id; also this saga's id.
  deploymentId: string;
  provider: Provider;
  // Native region code for the provider (e.g. 'eu-central-1', 'fra').
  providerRegion: string;
  name: string;
  hostname: string;
  // Unix epoch ms TTL, stamped onto the instance as an `expires-at` tag so the
  // reaper can reap it from the tag alone if its ledger row is ever lost.
  expiresAt: number;
}

export interface InstanceRefs {
  // Cloud handle the reaper deletes by: Lightsail instance name or Vultr id.
  instanceRef: string;
  // Vultr firewall-group id (deleted alongside the instance); null for Lightsail.
  firewallRef: string | null;
}

function lightsailClient(creds: CloudCreds) {
  return lightsail(creds.aws);
}

/** Mint a fresh ephemeral, tag-scoped Tailscale auth key for the node's first boot. */
export async function mintAuthKey(creds: CloudCreds): Promise<string> {
  const ts = tailscale(creds.tailscale);
  const key = await ts.mintEphemeralKey({
    tags: [CLOUD_EXIT_NODE_TAG],
    expirySeconds: KEY_EXPIRY_SECONDS,
  });
  return key.key;
}

/**
 * Render cloud-init and create the instance (+ firewall). Returns the cloud
 * handles to record. NOT idempotent on Vultr (a retry would create a second
 * VM), so the saga never retries this step.
 */
export async function provisionInstance(
  creds: CloudCreds,
  params: ProvisionParams,
  authKey: string,
): Promise<InstanceRefs> {
  const userData = renderCloudInit({
    authKey,
    hostname: params.hostname,
    advertiseTags: CLOUD_EXIT_NODE_TAG,
  });

  const tagInput = { deploymentId: params.deploymentId, expiresAt: params.expiresAt };

  if (params.provider === 'aws') {
    const ls = lightsailClient(creds);
    await ls.createInstance({
      region: params.providerRegion,
      name: params.hostname,
      userData,
      tags: lightsailExitNodeTags(tagInput),
    });
    // Lightsail instances are looked up by name; that is the reaper's handle.
    return { instanceRef: params.hostname, firewallRef: null };
  }

  const vt = vultr(creds.vultrApiKey);
  const firewallRef = await vt.createFirewallGroup(`exit-node ${params.hostname}`);
  await vt.addExitNodeFirewallRules(firewallRef);
  const instance = await vt.createInstance({
    region: params.providerRegion,
    label: params.hostname,
    hostname: params.hostname,
    userData,
    firewallGroupId: firewallRef,
    tags: vultrExitNodeTags(tagInput),
  });
  return { instanceRef: instance.id, firewallRef };
}

/** Lightsail only: lock the public firewall to UDP 41641 (Vultr did this at create). */
export async function lockLightsailFirewall(
  creds: CloudCreds,
  instanceRef: string,
  region: string,
): Promise<void> {
  await lightsailClient(creds).lockToExitNodePort(region, instanceRef);
}

/**
 * One readiness probe. Returns the public IP once the instance is running and
 * has one, otherwise null (the saga sleeps and probes again).
 */
export async function checkInstanceReady(
  creds: CloudCreds,
  params: ProvisionParams,
  instanceRef: string,
): Promise<string | null> {
  if (params.provider === 'aws') {
    const instance = await lightsailClient(creds).getInstance(params.providerRegion, instanceRef);
    return instance.state?.name === 'running' && instance.publicIpAddress
      ? instance.publicIpAddress
      : null;
  }

  const instance = await vultr(creds.vultrApiKey).getInstance(instanceRef);
  return instance.status === 'active' && instance.main_ip && instance.main_ip !== '0.0.0.0'
    ? instance.main_ip
    : null;
}

export interface SagaOptions {
  pollAttempts?: number;
  pollIntervalMs?: number;
  sleep?: (ms: number) => Promise<void>;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Run the full provisioning saga: mint a key, create the instance, record its
 * handles, lock the firewall (AWS), poll for readiness, then mark active. Any
 * failure is recorded on the row as `failed` (the reaper reconciles it) and
 * swallowed — the saga runs detached, so there is no caller to throw to.
 */
export async function provisionSaga(
  deps: { creds: CloudCreds; db: Db },
  params: ProvisionParams,
  opts: SagaOptions = {},
): Promise<void> {
  const { creds, db } = deps;
  const pollAttempts = opts.pollAttempts ?? READY_POLL_ATTEMPTS;
  const pollIntervalMs = opts.pollIntervalMs ?? READY_POLL_INTERVAL_MS;
  const sleep = opts.sleep ?? defaultSleep;

  try {
    const authKey = await mintAuthKey(creds);
    const refs = await provisionInstance(creds, params, authKey);
    // Persist the handles as soon as the instance exists, so the reaper can find
    // it even if a later step fails.
    await db.updateInstanceRefs(params.deploymentId, refs.instanceRef, refs.firewallRef);

    if (params.provider === 'aws') {
      await lockLightsailFirewall(creds, refs.instanceRef, params.providerRegion);
    }

    let publicIp: string | null = null;
    for (let attempt = 1; attempt <= pollAttempts; attempt++) {
      publicIp = await checkInstanceReady(creds, params, refs.instanceRef);
      if (publicIp) break;
      if (attempt < pollAttempts) await sleep(pollIntervalMs);
    }
    if (!publicIp) {
      throw new Error(`instance ${refs.instanceRef} did not become ready in time`);
    }

    await db.markActive(params.deploymentId, publicIp);
    console.log(
      `provision ${params.deploymentId} active provider=${params.provider} ip=${publicIp}`,
    );
  } catch (err) {
    const message = err instanceof Error ? err.message : String(err);
    console.error(`provision ${params.deploymentId} failed: ${message}`);
    // Best effort: if even the failure write fails, the reaper still reaps the
    // VM by its expires-at tag and reconciles the stuck row past the threshold.
    try {
      await db.markFailed(params.deploymentId, message);
    } catch (writeErr) {
      const m = writeErr instanceof Error ? writeErr.message : String(writeErr);
      console.error(`provision ${params.deploymentId} failed to record failure: ${m}`);
    }
  }
}
