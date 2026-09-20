#!/usr/bin/env bash
# Assemble the x86-64 library tree the Valheim server is loaded against.
#
# Two architectures are in play and only one of them varies. The guest is
# always x86-64, because that is the only dedicated server build Iron Gate
# publishes, so this always reads the amd64 indices no matter which runner is
# building. The builder's own architecture decides one thing only: whether
# these packages could be installed rather than unpacked. On arm64 they cannot,
# because dpkg would have to execute amd64 maintainer scripts, so dpkg-deb -x
# is used on both. That is pure extraction and needs no emulation.
#
# Iron Gate's manual names libatomic1 and libpulse0 as the requirements beyond
# a base install; the rest is their runtime closure, resolved once with apt and
# pinned here as a literal so the tree cannot quietly grow a dependency.
set -euo pipefail

rootfs=${1:?usage: build-guest-rootfs.sh ROOTFS}
mirror=${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu}
suite=${UBUNTU_SUITE:-noble}

# libpulse-mainloop-glib0 is not in Iron Gate's list but libparty.so, the
# PlayFab Party transport the crossplay backend runs on, links it directly and
# silently fails to load without it.
packages=(
  ca-certificates libapparmor1 libasyncns0 libatomic1 libbsd0 libdbus-1-3
  libflac12t64 libglib2.0-0t64 libmp3lame0 libmpg123-0t64 libogg0 libopus0
  libpulse-mainloop-glib0 libpulse0 libsndfile1 libvorbis0a libvorbisenc2
  libx11-6 libx11-data libx11-xcb1 libxau6 libxcb1 libxdmcp6 openssl
)

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

# -updates and -security carry newer builds than the release pocket, so all
# three are read and the highest version of each package wins.
for pocket in "${suite}" "${suite}-updates" "${suite}-security"; do
  for component in main universe; do
    curl -fsSL --retry 3 \
      "${mirror}/dists/${pocket}/${component}/binary-amd64/Packages.gz" |
      gunzip >>"${work}/index"
  done
done

awk -v RS= -v OFS='\t' '
  {
    name = ""; version = ""; sha = ""; file = ""
    fields = split($0, line, "\n")
    for (i = 1; i <= fields; i++) {
      if (line[i] ~ /^Package: /)  name    = substr(line[i], 10)
      if (line[i] ~ /^Version: /)  version = substr(line[i], 10)
      if (line[i] ~ /^SHA256: /)   sha     = substr(line[i], 9)
      if (line[i] ~ /^Filename: /) file    = substr(line[i], 11)
    }
    if (name != "" && file != "") print name, version, sha, file
  }' "${work}/index" >"${work}/candidates"

for package in "${packages[@]}"; do
  best_version="" best_sha="" best_file=""
  while IFS=$'\t' read -r _ version sha file; do
    if [[ -z ${best_version} ]] || dpkg --compare-versions "${version}" gt "${best_version}"; then
      best_version=${version} best_sha=${sha} best_file=${file}
    fi
  done < <(awk -F'\t' -v p="${package}" '$1 == p' "${work}/candidates")

  if [[ -z ${best_file} ]]; then
    echo "error: ${package} not found in the ${suite} amd64 indices" >&2
    exit 1
  fi

  deb="${work}/${package}.deb"
  curl -fsSL --retry 3 -o "${deb}" "${mirror}/${best_file}"
  echo "${best_sha}  ${deb}" | sha256sum -c - >/dev/null
  dpkg-deb -x "${deb}" "${rootfs}"
  echo "unpacked ${package} ${best_version}"
done

# The unversioned .so symlinks live in libpulse-dev, which Iron Gate also
# lists. Recreating the two that matter avoids unpacking a headers package that
# drags in python3 and pkgconf for no runtime benefit.
ln -sf libpulse.so.0 "${rootfs}/usr/lib/x86_64-linux-gnu/libpulse.so"
ln -sf libpulse-simple.so.0 "${rootfs}/usr/lib/x86_64-linux-gnu/libpulse-simple.so"

# ca-certificates builds its bundle in a postinst, which never runs here, and
# the crossplay backend talks HTTPS to PlayFab.
mkdir -p "${rootfs}/etc/ssl/certs"
cat "${rootfs}"/usr/share/ca-certificates/mozilla/*.crt \
  >"${rootfs}/etc/ssl/certs/ca-certificates.crt"

rm -rf \
  "${rootfs}/usr/share/doc" \
  "${rootfs}/usr/share/man" \
  "${rootfs}/usr/share/locale" \
  "${rootfs}/var/lib/apt/lists" \
  "${rootfs}/var/cache"
