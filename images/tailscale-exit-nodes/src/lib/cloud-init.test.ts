import { describe, it } from 'vitest';
import { renderCloudInit } from './cloud-init.js';

const INPUT = {
  authKey: 'tskey-auth-EXAMPLE',
  hostname: 'demo-ab12cd34',
  advertiseTags: 'tag:exit-node',
};

describe('renderCloudInit', () => {
  it('emits a flat POSIX sh script with the dynamic values interpolated', ({ expect }) => {
    const script = renderCloudInit(INPUT);

    expect(script.startsWith('#!/bin/sh\n')).toBe(true);
    expect(script).toContain('add_flag_with_value "authkey" "tskey-auth-EXAMPLE"');
    expect(script).toContain('--hostname=\\"demo-ab12cd34\\"');
    expect(script).toContain('--advertise-tags=\\"tag:exit-node\\"');
  });

  it('bakes in the exit-node-optimal defaults', ({ expect }) => {
    const script = renderCloudInit(INPUT);

    expect(script).toContain('--port=41641'); // matches the cloud firewall rule
    expect(script).toContain('tailscale set --advertise-exit-node="true"');
    expect(script).toContain('tailscale set --snat-subnet-routes="true"');
    expect(script).toContain('tailscale set --netfilter-mode="on"');
    expect(script).toContain('tailscale set --stateful-filtering="true"');
    expect(script).toContain('tailscale set --auto-update');
    expect(script).toContain('net.ipv4.ip_forward = 1');
  });

  it('preserves genuine bash parameter expansions verbatim', ({ expect }) => {
    const script = renderCloudInit(INPUT);

    expect(script).toContain('cat "${rv_value#file:}"');
    expect(script).toContain('eval "${rv_value#command:}"');
    // line continuations and escaped quotes survive
    expect(script).toContain('rx-gro-list off || \\');
    expect(script).toContain('--json=\\"false\\"');
  });

  it('leaves no unresolved Terraform template tokens', ({ expect }) => {
    const script = renderCloudInit(INPUT);

    expect(script).not.toMatch(/%\{/); // no %{ if } directives
    expect(script).not.toMatch(/\$\{[A-Z]/); // no ${UPPERCASE} templatefile vars
  });

  it('throws when a required input is empty', ({ expect }) => {
    expect(() => renderCloudInit({ ...INPUT, authKey: '' })).toThrow(/requires non-empty/);
    expect(() => renderCloudInit({ ...INPUT, hostname: '' })).toThrow(/requires non-empty/);
    expect(() => renderCloudInit({ ...INPUT, advertiseTags: '' })).toThrow(/requires non-empty/);
  });
});
