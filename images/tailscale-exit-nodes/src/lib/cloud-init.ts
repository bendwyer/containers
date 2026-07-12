// Renders the exit-node bootstrap as a flat POSIX `sh` user_data script:
// install Tailscale, authenticate with a fresh ephemeral key, and advertise the
// host as an exit node. Only the three per-deployment inputs below vary; the
// rest is constant. The body uses String.raw so bash `$`, `\`, and `"` survive
// without escaping.

export interface CloudInitInput {
  /** A freshly-minted ephemeral Tailscale auth key (tag-scoped, reusable=false, ephemeral=true). */
  authKey: string;
  /** Node hostname, e.g. `${name}-${shortDeploymentId}`. */
  hostname: string;
  /** Exit-node tag(s), comma-separated, e.g. `tag:exit-node`. */
  advertiseTags: string;
}

export function renderCloudInit({ authKey, hostname, advertiseTags }: CloudInitInput): string {
  if (!authKey || !hostname || !advertiseTags) {
    throw new Error('renderCloudInit requires non-empty authKey, hostname, and advertiseTags');
  }

  // Genuine bash parameter expansions (`${var#prefix}`); kept out of the
  // String.raw template so they are not treated as JS interpolations.
  const stripFilePrefix = '${rv_value#file:}';
  const stripCommandPrefix = '${rv_value#command:}';

  return String.raw`#!/bin/sh
set -eu

log() {
  echo "[$(date -u +%FT%TZ)] $1"
}

log "starting Tailscale exit-node bootstrap"

# IP forwarding -----------------------------------------------------------

cat > /etc/sysctl.d/99-tailscale-exit-node.conf <<'SYSCTL'
net.ipv4.ip_forward = 1
net.ipv6.conf.all.forwarding = 1
SYSCTL
sysctl -p /etc/sysctl.d/99-tailscale-exit-node.conf

# UDP GRO offload (kernel >= 6.2 + ethtool) ------------------------------
# https://tailscale.com/s/ethtool-config-udp-grow

apply_udp_offloads() {
  if ! command -v ethtool >/dev/null 2>&1; then
    log "ethtool not present; skipping UDP offload"
    return 0
  fi

  udp_kernel=$(uname -r | cut -d'-' -f1 | cut -d'.' -f1,2)
  if [ "$(printf '%s\n' "$udp_kernel" "6.2" | sort -V | tail -n1)" != "$udp_kernel" ]; then
    log "kernel $udp_kernel < 6.2; skipping UDP offload"
    return 0
  fi

  udp_netdev=$(ip route show default | awk '/default/ {print $5}' | head -n1)
  if [ -z "$udp_netdev" ]; then
    for dev in /sys/class/net/*; do
      if [ "$dev" != "/sys/class/net/lo" ]; then
        udp_netdev=$(basename "$dev")
        break
      fi
    done
  fi
  [ -z "$udp_netdev" ] && udp_netdev="eth0"

  log "enabling UDP GRO forwarding on $udp_netdev"
  ethtool -K "$udp_netdev" rx-udp-gro-forwarding on rx-gro-list off || \
    log "UDP offload tweak failed (non-fatal)"
}

apply_udp_offloads

# Install Tailscale -------------------------------------------------------

install_tailscale() {
  export TRACK="stable"
  install_i=1
  while [ "$install_i" -le 3 ]; do
    if curl -fsSL https://tailscale.com/install.sh | sh; then
      if command -v tailscale >/dev/null 2>&1; then
        log "Tailscale installed: $(tailscale version | head -n1)"
        return 0
      fi
    fi
    log "install attempt $install_i failed; retrying in 5s"
    sleep 5
    install_i=$((install_i + 1))
  done
  log "Tailscale installation failed after 3 attempts"
  return 1
}

install_tailscale

# tailscaled systemd drop-in ---------------------------------------------

mkdir -p /etc/systemd/system/tailscaled.service.d

FLAGS=""
FLAGS="$FLAGS --port=41641"

STATE_FLAG="--state=/var/lib/tailscale/tailscaled.state"
SOCKET_FLAG="--socket=/run/tailscale/tailscaled.sock"

cat > /etc/systemd/system/tailscaled.service.d/exit-node.conf <<CONF
[Service]
ExecStart=
ExecStart=/usr/sbin/tailscaled $STATE_FLAG $SOCKET_FLAG $FLAGS
CONF

systemctl daemon-reload
if systemctl is-active --quiet tailscaled; then
  systemctl restart tailscaled
fi

# tailscale up + tailscale set --------------------------------------------

resolve_value() {
  rv_value="$1"
  case "$rv_value" in
    file:*)
      cat "${stripFilePrefix}" | tr -d '\n'
      ;;
    command:*)
      eval "${stripCommandPrefix}" | tr -d '\n'
      ;;
    *)
      printf '%s' "$rv_value"
      ;;
  esac
}

add_flag_with_value() {
  af_flag="$1"
  af_value="$2"
  if [ -n "$af_value" ]; then
    af_resolved=$(resolve_value "$af_value")
    tailscale_cmd="$tailscale_cmd --$af_flag=\"$af_resolved\""
  fi
}

bring_up_tailscale() {
  up_i=1
  while [ "$up_i" -le 3 ]; do
    systemctl enable --now tailscaled

    tailscale_cmd="tailscale up \
      --json=\"false\" \
      --login-server=\"https://controlplane.tailscale.com\" \
      --reset=\"false\" \
      --timeout=\"0s\" \
      --force-reauth=\"false\""

    add_flag_with_value "authkey" "${authKey}"
    add_flag_with_value "id-token" ""
    add_flag_with_value "client-id" ""
    add_flag_with_value "client-secret" ""

    tailscale_cmd="$tailscale_cmd --hostname=\"${hostname}\""
    tailscale_cmd="$tailscale_cmd --advertise-tags=\"${advertiseTags}\""

    if eval "$tailscale_cmd"; then
      tailscale set --accept-dns="true"
      tailscale set --accept-routes="false"
      tailscale set --advertise-exit-node="true"
      tailscale set --advertise-connector="false"
      tailscale set --shields-up="false"
      tailscale set --ssh="false"
      tailscale set --snat-subnet-routes="true"
      tailscale set --netfilter-mode="on"
      tailscale set --stateful-filtering="true"

      tailscale set --auto-update

      log "tailscale up + tailscale set complete"
      return 0
    fi

    log "tailscale up attempt $up_i failed; sleeping 5s"
    sleep 5
    up_i=$((up_i + 1))
  done

  log "tailscale up failed after 3 attempts"
  return 1
}

bring_up_tailscale

# Logout on shutdown ------------------------------------------------------

cat > /etc/systemd/system/tailscale-logout.service <<'UNIT'
[Unit]
Description=Tailscale logout on shutdown
After=tailscaled.service
Requires=tailscaled.service

[Service]
Type=oneshot
RemainAfterExit=true
ExecStart=/bin/true
ExecStop=/usr/bin/tailscale logout

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now tailscale-logout.service

log "exit-node bootstrap complete"
`;
}
