import { z } from 'zod';

// Cloud provider credentials threaded into the saga (provisioning) and the
// reaper (sweeps). Sourced from the environment (mounted secrets) at deploy time.
export interface CloudCreds {
  aws: { accessKeyId: string; secretAccessKey: string };
  vultrApiKey: string;
  tailscale: { clientId: string; clientSecret: string };
}

export interface Config {
  databaseUrl: string;
  port: number;
  cloud: CloudCreds;
}

const EnvSchema = z.object({
  DATABASE_URL: z.string().min(1),
  PORT: z.coerce.number().int().positive().default(8080),
  AWS_ACCESS_KEY_ID: z.string().min(1),
  AWS_SECRET_ACCESS_KEY: z.string().min(1),
  VULTR_API_KEY: z.string().min(1),
  TAILSCALE_OAUTH_CLIENT_ID: z.string().min(1),
  TAILSCALE_OAUTH_CLIENT_SECRET: z.string().min(1),
});

/** Read and validate the runtime config from the environment. Throws on a missing var. */
export function loadConfig(env: NodeJS.ProcessEnv = process.env): Config {
  const parsed = EnvSchema.safeParse(env);
  if (!parsed.success) {
    const missing = parsed.error.issues.map((i) => i.path.join('.')).join(', ');
    throw new Error(`invalid environment configuration: ${missing}`);
  }
  const e = parsed.data;
  return {
    databaseUrl: e.DATABASE_URL,
    port: e.PORT,
    cloud: {
      aws: { accessKeyId: e.AWS_ACCESS_KEY_ID, secretAccessKey: e.AWS_SECRET_ACCESS_KEY },
      vultrApiKey: e.VULTR_API_KEY,
      tailscale: {
        clientId: e.TAILSCALE_OAUTH_CLIENT_ID,
        clientSecret: e.TAILSCALE_OAUTH_CLIENT_SECRET,
      },
    },
  };
}
