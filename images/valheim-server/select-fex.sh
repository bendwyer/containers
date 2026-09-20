#!/usr/bin/env bash
# Choose which of the installed FEX builds this host can run, and print its
# directory.
#
# The three builds are compiled against armv8.0-a, armv8.2-a and armv8.4-a
# baselines, so picking one too high faults with SIGILL somewhere unpredictable
# rather than refusing to start. Selection is therefore made twice: once from
# the HWCAP flags the kernel reports, which says what the compiler was allowed
# to emit, and once by actually running a guest binary, which catches a
# mismatch the flags did not predict. A host that fails the run steps down.
#
# Features listed are the ones mandatory at each baseline that carry a HWCAP
# bit: v8.1 added LSE and RDM, v8.2 added DC CVAP, v8.4 added LSE2, LRCPC2 and
# FlagM.
set -euo pipefail

fexdir=${FEX_DIR:-/opt/fex}
rootfs=${FEX_ROOTFS:?FEX_ROOTFS must be set}

# FEX creates its config directory and its server socket under HOME. When it
# cannot, it fails with "Couldn't connect to FEXServer socket", which says
# nothing about permissions and sends you looking in the wrong place.
if [[ -z ${HOME:-} || ! -w ${HOME} ]]; then
  echo "error: HOME (${HOME:-unset}) must exist and be writable for FEX to start its server" >&2
  exit 1
fi

declare -A required=(
  [armv8.4]="atomics asimdrdm dcpop uscat ilrcpc flagm"
  [armv8.2]="atomics asimdrdm dcpop"
  [armv8.0]=""
)

# The kernel sanitises HWCAP across big.LITTLE, so every core reports the same
# feature set and the first line is authoritative.
features=" $(awk -F': ' '/^Features/ {print $2; exit}' /proc/cpuinfo) "

supported() {
  local feature
  for feature in ${required[$1]}; do
    [[ ${features} == *" ${feature} "* ]] || return 1
  done
}

# The probe must not inherit the caller's guest environment. An LD_PRELOAD
# meant for the game is injected into the probe binary too, which kills it and
# is then reported as "this CPU cannot run this build", a diagnosis with
# nothing to do with the CPU.
runs() {
  [[ "$(env -u LD_PRELOAD -u LD_LIBRARY_PATH "$1/bin/FEX" /bin/uname -m 2>/dev/null)" == "x86_64" ]]
}

if [[ -n ${FEX_VARIANT:-} ]]; then
  candidates=("${FEX_VARIANT}")
else
  candidates=(armv8.4 armv8.2 armv8.0)
fi

export FEX_ROOTFS="${rootfs}"

for variant in "${candidates[@]}"; do
  dir="${fexdir}/${variant}"
  [[ -x "${dir}/bin/FEX" ]] || continue
  supported "${variant}" || continue
  if runs "${dir}"; then
    echo "${dir}"
    exit 0
  fi
  echo "warning: FEX ${variant} is advertised by this CPU but failed to run a guest binary" >&2
done

echo "error: no installed FEX build runs on this host (features:${features})" >&2
exit 1
