import { z } from 'zod';

// Thin Tailscale API v2 client. Exchanges OAuth client credentials for an
// access token, then mints a short-lived ephemeral auth key the exit-node VM
// uses to join the tailnet at boot. Ephemeral nodes self-remove from the
// tailnet shortly after they go offline, so no explicit device cleanup is
// needed on the reap path.

const API_BASE = 'https://api.tailscale.com/api/v2';
const DEFAULT_KEY_EXPIRY_SECONDS = 600;

const TokenResponse = z.object({ access_token: z.string() });
const KeyResponse = z.object({ id: z.string(), key: z.string() });

export interface TailscaleOAuthCredentials {
  clientId: string;
  clientSecret: string;
}

export interface MintEphemeralKeyInput {
  /** Tags the key (and resulting node) is scoped to, e.g. `['tag:exit-node']`. Required for OAuth-minted keys. */
  tags: string[];
  expirySeconds?: number;
}

export interface EphemeralKey {
  id: string;
  key: string;
}

export function tailscale(creds: TailscaleOAuthCredentials) {
  async function accessToken(): Promise<string> {
    const res = await fetch(`${API_BASE}/oauth/token`, {
      method: 'POST',
      headers: { 'content-type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams({
        grant_type: 'client_credentials',
        client_id: creds.clientId,
        client_secret: creds.clientSecret,
      }),
    });
    if (!res.ok) {
      throw new Error(`Tailscale OAuth token request failed: ${res.status} ${await res.text()}`);
    }
    return TokenResponse.parse(await res.json()).access_token;
  }

  return {
    /** Mint a tag-scoped, reusable=false, ephemeral, preauthorized auth key (default 600s expiry). */
    async mintEphemeralKey(input: MintEphemeralKeyInput): Promise<EphemeralKey> {
      const token = await accessToken();
      const res = await fetch(`${API_BASE}/tailnet/-/keys`, {
        method: 'POST',
        headers: {
          authorization: `Bearer ${token}`,
          'content-type': 'application/json',
        },
        body: JSON.stringify({
          capabilities: {
            devices: {
              create: {
                reusable: false,
                ephemeral: true,
                preauthorized: true,
                tags: input.tags,
              },
            },
          },
          expirySeconds: input.expirySeconds ?? DEFAULT_KEY_EXPIRY_SECONDS,
        }),
      });
      if (!res.ok) {
        throw new Error(`Tailscale key creation failed: ${res.status} ${await res.text()}`);
      }
      return KeyResponse.parse(await res.json());
    },
  };
}
