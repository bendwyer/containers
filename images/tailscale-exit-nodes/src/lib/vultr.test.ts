import { afterEach, describe, it, vi } from 'vitest';
import { vultr } from './vultr.js';

const API_KEY = 'VULTREXAMPLEKEY';

interface Captured {
  method: string;
  path: string;
  auth: string | null;
  body: unknown;
}

function mockFetch(reply: (path: string, method: string) => Response): Captured[] {
  const calls: Captured[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    const url = new URL(req.url);
    const text = await req.text();
    calls.push({
      method: req.method,
      path: url.pathname + url.search,
      auth: req.headers.get('authorization'),
      body: text ? JSON.parse(text) : undefined,
    });
    return reply(url.pathname, req.method);
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe('vultr client', () => {
  it('createFirewallGroup returns the new group id and sends the bearer token', async ({
    expect,
  }) => {
    const calls = mockFetch(() => Response.json({ firewall_group: { id: 'fwg-123' } }));

    const id = await vultr(API_KEY).createFirewallGroup('exit-node ab12');

    expect(id).toBe('fwg-123');
    expect(calls[0].method).toBe('POST');
    expect(calls[0].path).toBe('/v2/firewalls');
    expect(calls[0].auth).toBe('Bearer VULTREXAMPLEKEY');
    expect(calls[0].body).toEqual({ description: 'exit-node ab12' });
  });

  it('addExitNodeFirewallRules opens UDP 41641 for v4 and v6', async ({ expect }) => {
    const calls = mockFetch(() => Response.json({ firewall_rule: {} }));

    await vultr(API_KEY).addExitNodeFirewallRules('fwg-123');

    expect(calls).toHaveLength(2);
    expect(calls[0].body).toEqual({
      ip_type: 'v4',
      protocol: 'udp',
      subnet: '0.0.0.0',
      subnet_size: 0,
      port: '41641',
    });
    expect(calls[1].body).toMatchObject({ ip_type: 'v6', subnet: '::', port: '41641' });
  });

  it('createInstance base64-encodes user_data and applies exit-node defaults', async ({
    expect,
  }) => {
    const calls = mockFetch(() => Response.json({ instance: { id: 'inst-1', region: 'fra' } }));

    const instance = await vultr(API_KEY).createInstance({
      region: 'fra',
      label: 'exit-node-ab12',
      hostname: 'exit-node-ab12',
      userData: '#!/bin/sh\ntrue\n',
      firewallGroupId: 'fwg-123',
      tags: ['exit-node', 'ab12'],
    });

    expect(instance.id).toBe('inst-1');
    expect(calls[0].body).toMatchObject({
      region: 'fra',
      plan: 'vc2-1c-1gb',
      os_id: 2284,
      user_data: btoa('#!/bin/sh\ntrue\n'),
      firewall_group_id: 'fwg-123',
      tags: ['exit-node', 'ab12'],
      enable_ipv6: true,
      ddos_protection: false,
      activation_email: false,
    });
  });

  it('listInstances filters by tag and paginates wide', async ({ expect }) => {
    const calls = mockFetch(() =>
      Response.json({ instances: [{ id: 'inst-1', tags: ['exit-node'] }] }),
    );

    const instances = await vultr(API_KEY).listInstances('exit-node');

    expect(instances).toHaveLength(1);
    expect(calls[0].path).toBe('/v2/instances?per_page=500&tag=exit-node');
  });

  it('deleteInstance issues a DELETE and tolerates 204', async ({ expect }) => {
    const calls = mockFetch(() => new Response(null, { status: 204 }));

    await vultr(API_KEY).deleteInstance('inst-1');

    expect(calls[0].method).toBe('DELETE');
    expect(calls[0].path).toBe('/v2/instances/inst-1');
  });

  it('throws with status and body on error', async ({ expect }) => {
    mockFetch(() => new Response('bad key', { status: 401 }));

    await expect(vultr(API_KEY).getInstance('inst-1')).rejects.toThrow(
      /Vultr GET \/instances\/inst-1 failed: 401 bad key/,
    );
  });
});
