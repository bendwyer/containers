import { afterEach, describe, it, vi } from 'vitest';
import { tailscale } from './tailscale.js';

const CREDS = { clientId: 'tskey-client-EXAMPLE', clientSecret: 'tskey-secret-EXAMPLE' };

interface Captured {
  url: string;
  auth: string | null;
  contentType: string | null;
  body: string;
}

function mockFetch(handler: (url: URL) => Response): Captured[] {
  const calls: Captured[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    calls.push({
      url: req.url,
      auth: req.headers.get('authorization'),
      contentType: req.headers.get('content-type'),
      body: await req.text(),
    });
    return handler(new URL(req.url));
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe('tailscale client', () => {
  it('exchanges client credentials then mints a tag-scoped ephemeral key', async ({ expect }) => {
    const calls = mockFetch((url) => {
      if (url.pathname === '/api/v2/oauth/token') {
        return Response.json({ access_token: 'tok-abc', token_type: 'Bearer', expires_in: 3600 });
      }
      if (url.pathname === '/api/v2/tailnet/-/keys') {
        return Response.json({ id: 'k123', key: 'tskey-auth-MINTED' });
      }
      throw new Error(`unexpected ${url.pathname}`);
    });

    const result = await tailscale(CREDS).mintEphemeralKey({ tags: ['tag:exit-node'] });

    expect(result).toEqual({ id: 'k123', key: 'tskey-auth-MINTED' });

    // token exchange is form-encoded with the client credentials grant
    const token = calls[0];
    expect(token.contentType).toContain('application/x-www-form-urlencoded');
    expect(token.body).toContain('grant_type=client_credentials');
    expect(token.body).toContain('client_id=tskey-client-EXAMPLE');

    // key request is bearer-authed with the freshly obtained token
    const key = calls[1];
    expect(key.auth).toBe('Bearer tok-abc');
    expect(JSON.parse(key.body)).toEqual({
      capabilities: {
        devices: {
          create: {
            reusable: false,
            ephemeral: true,
            preauthorized: true,
            tags: ['tag:exit-node'],
          },
        },
      },
      expirySeconds: 600,
    });
  });

  it('honors a custom expiry', async ({ expect }) => {
    const calls = mockFetch((url) =>
      url.pathname.endsWith('/token')
        ? Response.json({ access_token: 'tok' })
        : Response.json({ id: 'k', key: 'tskey-auth-X' }),
    );

    await tailscale(CREDS).mintEphemeralKey({ tags: ['tag:exit-node'], expirySeconds: 120 });

    expect(JSON.parse(calls[1].body).expirySeconds).toBe(120);
  });

  it('throws if the token exchange fails', async ({ expect }) => {
    mockFetch(() => new Response('invalid_client', { status: 401 }));

    await expect(tailscale(CREDS).mintEphemeralKey({ tags: ['tag:exit-node'] })).rejects.toThrow(
      /Tailscale OAuth token request failed: 401 invalid_client/,
    );
  });

  it('throws if key creation fails', async ({ expect }) => {
    mockFetch((url) =>
      url.pathname.endsWith('/token')
        ? Response.json({ access_token: 'tok' })
        : new Response('forbidden', { status: 403 }),
    );

    await expect(tailscale(CREDS).mintEphemeralKey({ tags: ['tag:exit-node'] })).rejects.toThrow(
      /Tailscale key creation failed: 403 forbidden/,
    );
  });
});
