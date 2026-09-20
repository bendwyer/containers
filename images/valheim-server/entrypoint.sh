#!/usr/bin/env bash
set -euo pipefail

# Steam app 896660 is the dedicated server tool; 896661 is its Linux depot.
# SteamAppId is the game (892970), not the tool, which is what the shipped
# start_server.sh exports and what Steamworks authenticates against.
readonly STEAM_APP=896660
readonly STEAM_DEPOT=896661
export SteamAppId=892970

# App 1007 is the Steamworks SDK Redist and 1006 its Linux depot. The
# steamclient.so bundled in the game depot is from 2022 and predates the
# SteamGameServer015 interface the server asks for, so loading it gets as far
# as "Missing interface adapter" and then fails GameServer.Init().
readonly STEAM_REDIST_APP=1007
readonly STEAM_REDIST_DEPOT=1006

server_dir=${VALHEIM_SERVER_DIR:-/data/server}
save_dir=${VALHEIM_SAVE_DIR:-/data/worlds}
steam_dir=${VALHEIM_STEAMWORKS_DIR:-/data/steamworks}
rootfs=${FEX_ROOTFS:-/opt/valheim/rootfs}

# Secrets arrive as a mounted file more often than as an env var.
if [[ -n ${VALHEIM_PASSWORD_FILE:-} ]]; then
  VALHEIM_PASSWORD=$(<"${VALHEIM_PASSWORD_FILE}")
fi

name=${VALHEIM_SERVER_NAME:-}
world=${VALHEIM_WORLD:-Dedicated}
password=${VALHEIM_PASSWORD:-}
port=${VALHEIM_PORT:-2456}
public=${VALHEIM_PUBLIC:-1}

if [[ -z ${name} ]]; then
  echo "error: VALHEIM_SERVER_NAME is required" >&2
  exit 1
fi

# The server exits on these rather than starting with a bad configuration, so
# they are checked here where the message can say what to change.
if [[ ${public} == 1 && -z ${password} ]]; then
  echo "error: a public server needs VALHEIM_PASSWORD, or set VALHEIM_PUBLIC=0" >&2
  exit 1
fi
if [[ -n ${password} ]]; then
  if ((${#password} < 5)); then
    echo "error: VALHEIM_PASSWORD must be at least 5 characters" >&2
    exit 1
  fi
  if [[ ${name} == *"${password}"* || ${world} == *"${password}"* ]]; then
    echo "error: VALHEIM_PASSWORD must not appear in the server or world name" >&2
    exit 1
  fi
fi

mkdir -p "${server_dir}" "${save_dir}" "${steam_dir}" "${HOME}"

# Steam is reachable or it is not, and the difference is usually seconds. The
# delay doubles to a ceiling so a brief outage costs a few seconds while a long
# one still gives up in bounded time rather than hanging the start forever.
retry() {
  local attempts=${VALHEIM_DOWNLOAD_ATTEMPTS:-6}
  local delay=${VALHEIM_DOWNLOAD_DELAY:-10}
  local n=1
  while true; do
    if "$@"; then
      return 0
    fi
    if ((n >= attempts)); then
      return 1
    fi
    echo "==> attempt ${n} of ${attempts} failed, retrying in ${delay}s"
    sleep "${delay}"
    n=$((n + 1))
    delay=$((delay * 2))
    ((delay > 120)) && delay=120
  done
}

if [[ ${VALHEIM_SKIP_UPDATE:-false} != "true" ]]; then
  echo "==> updating Valheim server files in ${server_dir}"
  download=(DepotDownloader -app "${STEAM_APP}" -depot "${STEAM_DEPOT}" -dir "${server_dir}")
  # An escape hatch for rolling back a bad build, not a default. Pinning makes
  # the server drift behind clients that update themselves, and a world saved
  # by a newer build may not load on an older one.
  [[ -n ${VALHEIM_MANIFEST:-} ]] && download+=(-manifest "${VALHEIM_MANIFEST}")
  [[ ${VALHEIM_VALIDATE:-false} == "true" ]] && download+=(-validate)

  # An update check must not be able to take down a server that was running.
  # Restarts here are unattended, so giving up on a Steam outage would turn a
  # stale server into no server. Start what is installed and say so loudly.
  if ! retry "${download[@]}"; then
    if [[ -f "${server_dir}/valheim_server.x86_64" ]]; then
      echo "==> WARNING: Steam unreachable, starting the installed build instead."
      echo "==> WARNING: it may be older than clients expect, and they will not be able to join."
    else
      echo "error: Steam is unreachable and ${server_dir} has no game files to fall back on" >&2
      exit 1
    fi
  fi

  # Only the 64-bit build is wanted; the depot's top-level copies are 32-bit.
  echo "==> updating Steamworks redistributables in ${steam_dir}"
  printf 'regex:^linux64/\n' >"${steam_dir}/filelist.txt"
  if ! retry DepotDownloader -app "${STEAM_REDIST_APP}" -depot "${STEAM_REDIST_DEPOT}" \
    -dir "${steam_dir}" -filelist "${steam_dir}/filelist.txt"; then
    if [[ -f "${steam_dir}/linux64/steamclient.so" ]]; then
      echo "==> WARNING: Steam unreachable, keeping the installed Steamworks redistributable"
    else
      echo "error: Steam is unreachable and ${steam_dir} has no steamclient.so to fall back on" >&2
      exit 1
    fi
  fi
fi

if [[ ! -f "${server_dir}/valheim_server.x86_64" ]]; then
  echo "error: ${server_dir} has no valheim_server.x86_64" >&2
  exit 1
fi
chmod +x "${server_dir}/valheim_server.x86_64"

# Steamworks looks here before anywhere else, and libsteamwebrtc is loaded by
# steamclient itself, so the pair has to travel together.
if [[ ! -f "${steam_dir}/linux64/steamclient.so" ]]; then
  echo "error: no steamclient.so in ${steam_dir}; start once without VALHEIM_SKIP_UPDATE" >&2
  exit 1
fi
mkdir -p "${HOME}/.steam/sdk64"
cp -f "${steam_dir}/linux64/steamclient.so" "${steam_dir}/linux64/libsteamwebrtc.so" \
  "${HOME}/.steam/sdk64/"

args=(
  -name "${name}"
  -port "${port}"
  -world "${world}"
  -savedir "${save_dir}"
  -public "${public}"
)
[[ -n ${password} ]] && args+=(-password "${password}")

# Crossplay swaps the Steam backend for PlayFab rather than adding to it:
# without it only Steam users can see or join the server, which is why it is
# the default here.
[[ ${VALHEIM_CROSSPLAY:-true} == "true" ]] && args+=(-crossplay)
[[ -n ${VALHEIM_PRESET:-} ]] && args+=(-preset "${VALHEIM_PRESET}")
[[ -n ${VALHEIM_INSTANCE_ID:-} ]] && args+=(-instanceid "${VALHEIM_INSTANCE_ID}")
[[ -n ${VALHEIM_EXTRA_ARGS:-} ]] && read -ra extra <<<"${VALHEIM_EXTRA_ARGS}" && args+=("${extra[@]}")

cd "${server_dir}"

# libpulsecommon is installed in a private subdirectory that libpulse.so.0
# reaches through its RPATH. libparty.so links it directly and has no RPATH, so
# the directory has to be on the search path explicitly.
if [[ $(uname -m) == "aarch64" ]]; then
  fex=$(FEX_ROOTFS="${rootfs}" /usr/local/bin/select-fex.sh)
  echo "==> running under $(basename "${fex}") FEX"
  export FEX_ROOTFS="${rootfs}"
  # Paths here are resolved by the guest loader, so they are rootfs-relative.
  export LD_LIBRARY_PATH="/usr/lib/x86_64-linux-gnu/pulseaudio:${LD_LIBRARY_PATH:-}"
  runner=("${fex}/bin/FEX")
else
  # No emulator needed. The guest tree is built from the same Ubuntu release as
  # this image, so its extra libraries load against the host loader; only the
  # libraries Valheim needs beyond a base install are put on the path.
  export LD_LIBRARY_PATH="${rootfs}/usr/lib/x86_64-linux-gnu:${rootfs}/usr/lib/x86_64-linux-gnu/pulseaudio:${LD_LIBRARY_PATH:-}"
  runner=()
fi

redacted=()
for arg in "${args[@]}"; do
  if [[ -n ${password} && ${arg} == "${password}" ]]; then
    redacted+=("*****")
  else
    redacted+=("${arg}")
  fi
done
echo "==> starting: valheim_server.x86_64 ${redacted[*]}"

"${runner[@]}" ./valheim_server.x86_64 "${args[@]}" &
server=$!

# Valheim saves the world on SIGINT and Iron Gate's manual is explicit that the
# server must be stopped that way. Kubernetes sends SIGTERM, so it is
# translated here; give terminationGracePeriodSeconds room for the save.
shutdown() {
  echo "==> stopping server"
  kill -INT "${server}" 2>/dev/null || true
}
trap shutdown TERM INT

# wait returns as soon as a trapped signal is handled, which is long before the
# server has finished writing the world, so exiting on the first return would
# hand the container back to the runtime mid-save. Wait again until the child
# is genuinely gone.
status=0
while kill -0 "${server}" 2>/dev/null; do
  if wait "${server}"; then
    status=0
  else
    status=$?
  fi
  # The child was reaped between the liveness check and the wait.
  if ((status == 127)); then
    status=0
    break
  fi
done

exit "${status}"
