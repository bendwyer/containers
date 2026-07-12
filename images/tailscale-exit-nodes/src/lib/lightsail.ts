import { AwsClient } from 'aws4fetch';
import { z } from 'zod';

// Thin AWS Lightsail client for exit-node provisioning. Uses the Lightsail
// JSON API (`X-Amz-Target: Lightsail_20161128.<Action>`) over SigV4 via
// aws4fetch; region and service are inferred from the regional endpoint host.

const API_VERSION = 'Lightsail_20161128';

const DEFAULTS = {
  blueprintId: 'ubuntu_24_04',
  bundleId: 'nano_3_0', // smallest
  ipAddressType: 'dualstack', // IPv4 + IPv6
  exitNodePort: 41641,
} as const;

export interface LightsailCredentials {
  accessKeyId: string;
  secretAccessKey: string;
}

export interface LightsailTag {
  key: string;
  value?: string;
}

const InstanceSchema = z.object({
  name: z.string(),
  arn: z.string().optional(),
  publicIpAddress: z.string().optional(),
  ipv6Addresses: z.array(z.string()).optional(),
  state: z.object({ code: z.number(), name: z.string() }).optional(),
  tags: z.array(z.object({ key: z.string(), value: z.string().optional() })).optional(),
});
export type LightsailInstance = z.infer<typeof InstanceSchema>;

const GetInstanceResponse = z.object({ instance: InstanceSchema });
const GetInstancesResponse = z.object({ instances: z.array(InstanceSchema) });

export interface CreateLightsailInstanceInput {
  region: string;
  /** Instance name; also used by the Reaper to look the instance back up. */
  name: string;
  /** Rendered cloud-init script (plain text; Lightsail does NOT base64 it). */
  userData: string;
  /** Tags the Reaper sweeps by, e.g. `[{key:'exit-node'}, {key:'deployment_id', value}]`. */
  tags?: LightsailTag[];
  availabilityZone?: string;
  blueprintId?: string;
  bundleId?: string;
  ipAddressType?: string;
}

export function lightsail(creds: LightsailCredentials) {
  const aws = new AwsClient({
    accessKeyId: creds.accessKeyId,
    secretAccessKey: creds.secretAccessKey,
    service: 'lightsail',
  });

  async function call(region: string, action: string, body: unknown): Promise<unknown> {
    const res = await aws.fetch(`https://lightsail.${region}.amazonaws.com/`, {
      method: 'POST',
      headers: {
        'content-type': 'application/x-amz-json-1.1',
        'x-amz-target': `${API_VERSION}.${action}`,
      },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      throw new Error(`Lightsail ${action} failed: ${res.status} ${await res.text()}`);
    }
    return res.json();
  }

  return {
    /** Create the instance. Firewall is opened separately via lockToExitNodePort. */
    async createInstance(input: CreateLightsailInstanceInput): Promise<void> {
      await call(input.region, 'CreateInstances', {
        instanceNames: [input.name],
        availabilityZone: input.availabilityZone ?? `${input.region}a`,
        blueprintId: input.blueprintId ?? DEFAULTS.blueprintId,
        bundleId: input.bundleId ?? DEFAULTS.bundleId,
        ipAddressType: input.ipAddressType ?? DEFAULTS.ipAddressType,
        userData: input.userData,
        tags: input.tags ?? [],
      });
    },

    /** Lock the public firewall to UDP 41641 only (Tailscale); manage everything else over the tailnet. */
    async lockToExitNodePort(region: string, instanceName: string): Promise<void> {
      await call(region, 'PutInstancePublicPorts', {
        instanceName,
        portInfos: [
          {
            fromPort: DEFAULTS.exitNodePort,
            toPort: DEFAULTS.exitNodePort,
            protocol: 'udp',
            cidrs: ['0.0.0.0/0'],
            ipv6Cidrs: ['::/0'],
          },
        ],
      });
    },

    async getInstance(region: string, name: string): Promise<LightsailInstance> {
      const data = await call(region, 'GetInstance', { instanceName: name });
      return GetInstanceResponse.parse(data).instance;
    },

    /** List all instances in a region (for the Reaper's tag-reconciliation sweep). */
    async getInstances(region: string): Promise<LightsailInstance[]> {
      const data = await call(region, 'GetInstances', {});
      return GetInstancesResponse.parse(data).instances;
    },

    async deleteInstance(region: string, name: string): Promise<void> {
      await call(region, 'DeleteInstance', { instanceName: name, forceDeleteAddOns: true });
    },
  };
}
