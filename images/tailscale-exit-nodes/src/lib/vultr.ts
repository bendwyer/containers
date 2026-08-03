import { z } from 'zod';

// Thin Vultr API v2 client for exit-node provisioning (Bearer auth, raw fetch).
// Provisioning order: createFirewallGroup -> addExitNodeFirewallRules ->
// createInstance (with the group attached).

const API_BASE = 'https://api.vultr.com/v2';

const DEFAULTS = {
  plan: 'vc2-1c-1gb', // smallest standard
  osId: 2284, // Ubuntu 24.04 LTS x64 (verify against GET /v2/os)
  exitNodePort: '41641',
} as const;

const FirewallGroupResponse = z.object({
  firewall_group: z.object({ id: z.string() }),
});

const InstanceSchema = z.object({
  id: z.string(),
  label: z.string().optional(),
  hostname: z.string().optional(),
  main_ip: z.string().optional(),
  v6_main_ip: z.string().optional(),
  status: z.string().optional(),
  server_status: z.string().optional(),
  power_status: z.string().optional(),
  region: z.string().optional(),
  tags: z.array(z.string()).optional(),
  // Attached firewall group; the Reaper deletes it alongside the instance.
  firewall_group_id: z.string().optional(),
});
export type VultrInstance = z.infer<typeof InstanceSchema>;

const InstanceResponse = z.object({ instance: InstanceSchema });
const ListInstancesResponse = z.object({ instances: z.array(InstanceSchema) });

const RETRY_ATTEMPTS = 3;
const RETRY_BASE_DELAY_MS = 500;

class VultrHttpError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
    this.name = 'VultrHttpError';
  }
}

/**
 * Vultr serves transient 5xx often enough to fail a couple of percent of reaper
 * sweeps, and occasionally answers 200 with a body missing the documented field.
 * Retry those; anything else (a rejected key, a genuine 4xx) fails on the first
 * attempt so it still reaches the ExitNodeReaperErrors alert promptly.
 */
function isRetryable(err: unknown): boolean {
  if (err instanceof VultrHttpError) return err.status === 429 || err.status >= 500;
  if (err instanceof z.ZodError || err instanceof SyntaxError) return true;
  // fetch() reports connection failures as TypeError.
  return err instanceof TypeError;
}

/**
 * Retry only where a repeated call cannot leak a second resource: a 5xx on a
 * create may mean Vultr made the instance and lost the response.
 */
function isIdempotent(method: string): boolean {
  return method === 'GET' || method === 'DELETE';
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

export interface VultrOptions {
  sleep?: (ms: number) => Promise<void>;
}

export interface CreateVultrInstanceInput {
  region: string;
  label: string;
  hostname: string;
  /** Rendered cloud-init script (raw text, base64-encoded internally; Vultr requires it). */
  userData: string;
  firewallGroupId: string;
  /** Tags the Reaper sweeps by, e.g. `['exit-node', deploymentId]`. */
  tags?: string[];
  plan?: string;
  osId?: number;
}

export function vultr(apiKey: string, opts: VultrOptions = {}) {
  const sleep = opts.sleep ?? defaultSleep;

  // Schema parsing happens here rather than at the call site so a malformed body
  // is retried alongside the transport failures it travels with.
  async function callOnce(
    method: string,
    path: string,
    body: unknown,
    schema?: z.ZodType<unknown>,
  ): Promise<unknown> {
    const res = await fetch(`${API_BASE}${path}`, {
      method,
      headers: {
        authorization: `Bearer ${apiKey}`,
        ...(body === undefined ? {} : { 'content-type': 'application/json' }),
      },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    if (!res.ok) {
      throw new VultrHttpError(
        res.status,
        `Vultr ${method} ${path} failed: ${res.status} ${await res.text()}`,
      );
    }
    if (res.status === 204) return undefined;
    const data: unknown = await res.json();
    return schema ? schema.parse(data) : data;
  }

  async function call(method: string, path: string, body?: unknown): Promise<unknown>;
  async function call<T>(
    method: string,
    path: string,
    body: unknown,
    schema: z.ZodType<T>,
  ): Promise<T>;
  async function call(
    method: string,
    path: string,
    body?: unknown,
    schema?: z.ZodType<unknown>,
  ): Promise<unknown> {
    const attempts = isIdempotent(method) ? RETRY_ATTEMPTS : 1;
    for (let attempt = 1; ; attempt++) {
      try {
        return await callOnce(method, path, body, schema);
      } catch (err) {
        if (attempt >= attempts || !isRetryable(err)) throw err;
        await sleep(RETRY_BASE_DELAY_MS * 2 ** (attempt - 1));
      }
    }
  }

  return {
    async createFirewallGroup(description: string): Promise<string> {
      const data = await call('POST', '/firewalls', { description }, FirewallGroupResponse);
      return data.firewall_group.id;
    },

    /** Add the two allow rules (v4 + v6) for UDP 41641; everything else stays blocked. */
    async addExitNodeFirewallRules(groupId: string): Promise<void> {
      for (const ipType of ['v4', 'v6'] as const) {
        await call('POST', `/firewalls/${groupId}/rules`, {
          ip_type: ipType,
          protocol: 'udp',
          subnet: ipType === 'v4' ? '0.0.0.0' : '::',
          subnet_size: 0,
          port: DEFAULTS.exitNodePort,
        });
      }
    },

    async createInstance(input: CreateVultrInstanceInput): Promise<VultrInstance> {
      const data = await call(
        'POST',
        '/instances',
        {
          region: input.region,
          plan: input.plan ?? DEFAULTS.plan,
          os_id: input.osId ?? DEFAULTS.osId,
          label: input.label,
          hostname: input.hostname,
          user_data: btoa(input.userData),
          tags: input.tags ?? [],
          firewall_group_id: input.firewallGroupId,
          enable_ipv6: true,
          ddos_protection: false,
          activation_email: false,
        },
        InstanceResponse,
      );
      return data.instance;
    },

    async getInstance(id: string): Promise<VultrInstance> {
      const data = await call('GET', `/instances/${id}`, undefined, InstanceResponse);
      return data.instance;
    },

    /** List instances (optionally filtered by tag) for the Reaper's sweep. */
    async listInstances(tag?: string): Promise<VultrInstance[]> {
      const params = new URLSearchParams({ per_page: '500' });
      if (tag) params.set('tag', tag);
      const data = await call(
        'GET',
        `/instances?${params.toString()}`,
        undefined,
        ListInstancesResponse,
      );
      return data.instances;
    },

    async deleteInstance(id: string): Promise<void> {
      await call('DELETE', `/instances/${id}`);
    },

    /** Delete a firewall group (after its instance is gone) during a Reaper sweep. */
    async deleteFirewallGroup(id: string): Promise<void> {
      await call('DELETE', `/firewalls/${id}`);
    },
  };
}
