#!/usr/bin/env bash
# Install FEX from the upstream PPA, unpacked rather than apt-installed.
#
# The .deb's Depends list pulls in Qt5, zenity and OpenGL for FEXConfig, a GUI
# this image never runs. FEX itself needs only libc6, libgcc-s1 and libstdc++6,
# which the base image already has, so the package is extracted instead.
#
# All three CPU builds are installed side by side and chosen at runtime by
# select-fex.sh. They differ only in the -march baseline FEX's own host code is
# compiled against (armv8.0-a, armv8.2-a, armv8.4-a, all +crc), so the wrong
# one is a SIGILL rather than a graceful refusal, and the right one is a
# property of whatever host the image lands on, not of this build.
#
# Filenames are resolved out of the index rather than hardcoded so that
# FEX_VERSION stays load-bearing: a version the PPA does not publish fails the
# build, while Launchpad's packaging revision, which differs per release and
# per Ubuntu series, does not have to be tracked.
set -euo pipefail

version=${1:?usage: install-fex.sh FEX_VERSION DESTDIR}
destdir=${2:?usage: install-fex.sh FEX_VERSION DESTDIR}
ppa=${FEX_PPA:-https://ppa.launchpadcontent.net/fex-emu/fex/ubuntu}
series=${FEX_SERIES:-noble}
variants=(armv8.0 armv8.2 armv8.4)

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT

curl -fsSL --retry 3 "${ppa}/dists/${series}/main/binary-arm64/Packages.gz" |
  gunzip >"${work}/index"

for variant in "${variants[@]}"; do
  package="fex-emu-${variant}"

  read -r found_version sha file < <(
    awk -v RS= -v want="${package}" '
      {
        name = ""; version = ""; sha = ""; path = ""
        fields = split($0, line, "\n")
        for (i = 1; i <= fields; i++) {
          if (line[i] ~ /^Package: /)  name    = substr(line[i], 10)
          if (line[i] ~ /^Version: /)  version = substr(line[i], 10)
          if (line[i] ~ /^SHA256: /)   sha     = substr(line[i], 9)
          if (line[i] ~ /^Filename: /) path    = substr(line[i], 11)
        }
        if (name == want) print version, sha, path
      }' "${work}/index"
  )

  if [[ -z ${file:-} ]]; then
    echo "error: ${package} not published for ${series}" >&2
    exit 1
  fi

  # The PPA version is "2609-1~n": the FEX release, then Launchpad's packaging
  # revision. Only the release is pinned here.
  if [[ ${found_version%%-*} != "${version}" ]]; then
    echo "error: asked for FEX ${version} but ${series} publishes ${package} ${found_version}" >&2
    exit 1
  fi

  deb="${work}/${package}.deb"
  extracted="${work}/${variant}"
  curl -fsSL --retry 3 -o "${deb}" "${ppa}/${file}"
  echo "${sha}  ${deb}" | sha256sum -c - >/dev/null
  dpkg-deb -x "${deb}" "${extracted}"

  # FEXConfig is the Qt GUI whose dependencies were the reason for unpacking.
  # FEXRootFSFetcher downloads and mounts squashfs images, which needs FUSE and
  # is what the prebuilt guest tree replaces.
  rm -f "${extracted}/usr/bin/FEXConfig" "${extracted}/usr/bin/FEXRootFSFetcher"

  mkdir -p "${destdir}/${variant}"
  cp -a "${extracted}/usr/bin" "${destdir}/${variant}/bin"
  cp -a "${extracted}/usr/lib/aarch64-linux-gnu/fex-emu" "${destdir}/${variant}/lib"

  # Thunk libraries and AppConfig are identical across the three builds, so
  # they are installed once at the path FEX looks for them on.
  if [[ ! -d /usr/share/fex-emu ]]; then
    cp -a "${extracted}/usr/share/fex-emu" /usr/share/fex-emu
  fi

  echo "installed ${package} ${found_version}"
done
