# valheim-server

Valheim dedicated server, runnable on arm64.

Iron Gate publishes the dedicated server for x86-64 Linux only. Steam app 896660
has three platform depots, `windows` / `macos` / `linux`, with no architecture
tagging and no arm64 depot, and every native binary in the Linux depot is ELF
x86-64. On arm64 the server therefore runs under [FEX](https://fex-emu.com/),
a usermode x86-64 emulator. On amd64 the same image runs it directly.

## Why the image is built the way it is

**The game is downloaded at runtime, not baked into the image.** Iron Gate
grants no redistribution right for the server binaries, and this repository is
public. The first start of a container populates `VALHEIM_SERVER_DIR`, so give
it a persistent volume or every restart re-downloads about 2 GB.

**DepotDownloader, not steamcmd.** steamcmd is 32-bit x86, which would mean
emulating the downloader and carrying a second 32-bit guest tree just to fetch
files. DepotDownloader publishes a native linux-arm64 build, so the download
runs at full speed and only the game itself is emulated.

**`steamclient.so` comes from Valve, not from the game depot.** The copy the
Valheim depot ships under `docker/` was last touched in 2022 and does not carry
the `SteamGameServer015` interface the current server asks for, so it loads and
then fails with `Missing interface adapter`, which surfaces only as `Steam is
not initialized` and `Awake of network backend failed`. The entrypoint pulls
Steamworks SDK Redist (app 1007, depot 1006) instead and places it, with
`libsteamwebrtc.so`, in `~/.steam/sdk64`. This happens even with crossplay: the
server initialises Steam either way, and a failure there is fatal.

**An update check cannot take down a server that was running.** Restarts are
normally unattended, so a Steam outage must not turn a stale server into no
server. The download is retried with a doubling delay, and if it still fails
while game files are already installed the server starts on those and says so
loudly in the log. It fails only when there is nothing to fall back on. The
residual risk is a download that fails partway, leaving a mix of old and new
files that this will happily start; the common failure touches nothing, and
`VALHEIM_VALIDATE=true` is the lever if you suspect otherwise.

**The guest tree must not shadow what the runtime injects into `/etc`.** FEX
resolves a guest path inside the RootFS first and falls back to the host only
when it is absent. The stock image ships `resolv.conf` and `hosts` as empty
files, so the guest sees no nameservers and every lookup fails with `Cannot
resolve destination host`, which on the crossplay backend means PlayFab login
never succeeds. Both are deleted from the tree; `nsswitch.conf` is kept,
because glibc's resolver needs it and the guest's copy is the valid one. The
same rule produced the passwd fix below, and it is the first thing to check for
any new "works natively, fails under FEX" symptom.

**The guest tree carries a passwd entry for the runtime user.** PlayFab Party,
which the crossplay backend depends on, calls `getpwuid()` and dereferences
`pw_dir` without a null check. FEX resolves `/etc/passwd` inside the RootFS, so
a guest tree that does not describe the uid the container runs as returns NULL
and the server segfaults the moment `libparty.so` is loaded, before any game
code runs. The fault is a null dereference at `0x20`, the offset of `pw_dir`.
Native runs never hit it because there the process reads this image's own
passwd. The uid, gid and home directory are build args so the runtime user and
the guest entry cannot drift apart; the volume's `fsGroup` has to match them.

**The guest library tree is assembled with `dpkg-deb`, not apt.** An arm64
builder cannot run amd64 maintainer scripts, and pulling QEMU into every image
build to work around that costs more than it saves. `build-guest-rootfs.sh`
resolves packages out of the Ubuntu indices and unpacks them, which needs no
emulation and works identically on both build architectures.

**Ubuntu, where the rest of this repository uses Alpine.** The FEX packages are
built against Ubuntu noble and need `libc6 >= 2.38` and `libstdc++6 >= 13`, and
DepotDownloader ships a glibc .NET runtime; neither has a musl build. Matching
the runtime base to the series the guest tree is assembled from also keeps the
amd64 path valid, since there the host loader resolves those same libraries.

**All three FEX CPU builds are installed.** They differ in the `-march`
baseline FEX's own code is compiled against (`armv8.0-a`, `armv8.2-a`,
`armv8.4-a`), so the wrong one is a `SIGILL` rather than a clean refusal.
`select-fex.sh` picks one at startup from the kernel's HWCAP flags and confirms
the choice by running a guest binary, stepping down if that fails. Set
`FEX_VARIANT` to override.

## Configuration

| Variable | Default | Notes |
|---|---|---|
| `VALHEIM_SERVER_NAME` | | Required. |
| `VALHEIM_PASSWORD` | | Required when public. At least 5 characters, and must not appear in the server or world name. |
| `VALHEIM_PASSWORD_FILE` | | Read the password from a file instead. |
| `VALHEIM_WORLD` | `Dedicated` | Created if it does not exist. |
| `VALHEIM_PORT` | `2456` | Valheim uses this port and the next. |
| `VALHEIM_PUBLIC` | `1` | `0` keeps the server out of the browser. |
| `VALHEIM_CROSSPLAY` | `true` | See below. |
| `VALHEIM_PRESET` | | `Normal`, `Casual`, `Easy`, `Hard`, `Hardcore`, `Immersive`, `Hammer`. |
| `VALHEIM_INSTANCE_ID` | | Needed only when several servers share a port and MAC. |
| `VALHEIM_EXTRA_ARGS` | | Appended verbatim, for `-modifier`, `-saveinterval`, `-backups` and the rest. |
| `VALHEIM_SERVER_DIR` | `/data/server` | Game files. |
| `VALHEIM_SAVE_DIR` | `/data/worlds` | Worlds and the admin, banned and permitted lists. |
| `VALHEIM_STEAMWORKS_DIR` | `/data/steamworks` | Steamworks redistributables. |
| `VALHEIM_MANIFEST` | | Pin a depot manifest instead of taking whatever Steam serves. |
| `VALHEIM_SKIP_UPDATE` | `false` | Start without contacting Steam. |
| `VALHEIM_VALIDATE` | `false` | Checksum every file on update. |
| `VALHEIM_DOWNLOAD_ATTEMPTS` | `6` | Tries before giving up on Steam. |
| `VALHEIM_DOWNLOAD_DELAY` | `10` | Seconds before the first retry; doubles to a 120s ceiling. |

### Crossplay is a backend, not an addition

Per Iron Gate's manual: with `-crossplay` the server runs on the PlayFab
backend and players from any platform can join; without it the Steam backend is
used and only Steam users can see or join. It is a choice between the two, not
a flag that widens Steam's reach, which is why it defaults on here.

On crossplay the server reaches players through a relay and needs no inbound
port, only egress. Two consequences: the port still matters for distinguishing
servers that share a public address, and **a crossplay server cannot be joined
over a local IP or loopback**. Players on the same network still need the join
code or the public address.

Without crossplay the server needs UDP `2456-2457` reachable from outside.

## Running it

The server saves the world on `SIGINT`, which Iron Gate's manual is explicit
about. Kubernetes sends `SIGTERM`, so the entrypoint translates it; allow a
`terminationGracePeriodSeconds` long enough for the save to finish.

The container runs as uid 10001. Give the volume an `fsGroup` so it can write,
or the first `mkdir` fails.

`HOME` must exist and be writable. FEX creates its config directory and its
server socket there, and without it fails with `Couldn't connect to FEXServer
socket`, which does not mention permissions.

FEX resolves a guest path inside the RootFS first and falls back to the host
path when it is absent, which is how `/data` reaches the real volume. The
fallback uses `openat2` with `RESOLVE_IN_ROOT`, so a seccomp profile that
blocks `openat2` will break it.

## Known limits

Emulation is the only way this runs on arm64 and it has not been validated at
load. FEX's correctness for multithreaded guests depends on TSO emulation,
which is on by default and is the slower, safer setting; leaving it alone is
deliberate for something that owns save files.
