import { afterEach, describe, it, vi } from 'vitest';
import { lightsail } from './lightsail.js';

const CREDS = { accessKeyId: 'AKIAEXAMPLE', secretAccessKey: 'wJalrXUtnFEMIexampleKEY' };

interface Captured {
  url: string;
  target: string | null;
  body: unknown;
}

// aws4fetch calls global fetch with a signed Request; capture it and reply.
function mockFetch(reply: (target: string) => Response): Captured[] {
  const calls: Captured[] = [];
  vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
    const req = input instanceof Request ? input : new Request(input, init);
    const target = req.headers.get('x-amz-target');
    calls.push({ url: req.url, target, body: JSON.parse(await req.text()) });
    return reply(target ?? '');
  });
  return calls;
}

afterEach(() => vi.restoreAllMocks());

describe('lightsail client', () => {
  it('createInstance posts CreateInstances with exit-node defaults to the regional endpoint', async ({
    expect,
  }) => {
    const calls = mockFetch(() => Response.json({ operations: [] }));

    await lightsail(CREDS).createInstance({
      region: 'eu-central-1',
      name: 'exit-node-ab12',
      userData: '#!/bin/sh\ntrue\n',
      tags: [{ key: 'exit-node' }, { key: 'deployment_id', value: 'ab12' }],
    });

    expect(calls).toHaveLength(1);
    const [call] = calls;
    expect(call.url).toBe('https://lightsail.eu-central-1.amazonaws.com/');
    expect(call.target).toBe('Lightsail_20161128.CreateInstances');
    expect(call.body).toMatchObject({
      instanceNames: ['exit-node-ab12'],
      availabilityZone: 'eu-central-1a',
      blueprintId: 'ubuntu_24_04',
      bundleId: 'nano_3_0',
      ipAddressType: 'dualstack',
      userData: '#!/bin/sh\ntrue\n',
      tags: [{ key: 'exit-node' }, { key: 'deployment_id', value: 'ab12' }],
    });
  });

  it('lockToExitNodePort opens only UDP 41641 for v4 + v6', async ({ expect }) => {
    const calls = mockFetch(() => Response.json({ operation: {} }));

    await lightsail(CREDS).lockToExitNodePort('us-west-2', 'exit-node-ab12');

    expect(calls[0].target).toBe('Lightsail_20161128.PutInstancePublicPorts');
    expect(calls[0].body).toEqual({
      instanceName: 'exit-node-ab12',
      portInfos: [
        {
          fromPort: 41641,
          toPort: 41641,
          protocol: 'udp',
          cidrs: ['0.0.0.0/0'],
          ipv6Cidrs: ['::/0'],
        },
      ],
    });
  });

  it('getInstance parses and returns the instance', async ({ expect }) => {
    mockFetch(() =>
      Response.json({
        instance: {
          name: 'exit-node-ab12',
          publicIpAddress: '203.0.113.7',
          state: { code: 16, name: 'running' },
        },
      }),
    );

    const instance = await lightsail(CREDS).getInstance('eu-central-1', 'exit-node-ab12');
    expect(instance.state?.name).toBe('running');
    expect(instance.publicIpAddress).toBe('203.0.113.7');
  });

  it('throws with the status and body on an API error', async ({ expect }) => {
    mockFetch(() => new Response('access denied', { status: 403 }));

    await expect(lightsail(CREDS).getInstances('eu-central-1')).rejects.toThrow(
      /Lightsail GetInstances failed: 403 access denied/,
    );
  });
});
