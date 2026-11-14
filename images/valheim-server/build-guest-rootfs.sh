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
# Only the roots are named. apt resolves their closure against the guest's own
# dpkg database, so the tree gets exactly what the base image lacks, and the
# release is whatever the guest base image is: package names that change
# between releases (t64, libflac soname bumps) never have to be edited here.
set -euo pipefail

rootfs=${1:?usage: build-guest-rootfs.sh ROOTFS}
mirror=${UBUNTU_MIRROR:-http://archive.ubuntu.com/ubuntu}
# shellcheck source=/dev/null
suite=$(. "${rootfs}/etc/os-release" && echo "${VERSION_CODENAME:?}")

# Iron Gate's manual names libatomic1 and libpulse0. libpulse-mainloop-glib0 is
# not in its list, but libparty.so, the PlayFab Party transport the crossplay
# backend runs on, links it directly and silently fails to load without it.
packages=(ca-certificates libatomic1 libpulse0 libpulse-mainloop-glib0)

work=$(mktemp -d)
trap 'rm -rf "${work}"' EXIT
mkdir -p "${work}/lists/partial" "${work}/cache/archives/partial" "${work}/sources.list.d"

# -updates and -security carry newer builds than the release pocket.
for pocket in "${suite}" "${suite}-updates" "${suite}-security"; do
  echo "deb [arch=amd64 signed-by=${rootfs}/usr/share/keyrings/ubuntu-archive-keyring.gpg] ${mirror} ${pocket} main universe"
done >"${work}/sources.list"

# A private apt state pointed at the guest, so the builder's own architecture,
# sources and package database play no part.
apt_opts=(
  -o "APT::Architecture=amd64"
  -o "APT::Architectures=amd64"
  -o "Dir::Etc::SourceList=${work}/sources.list"
  -o "Dir::Etc::SourceParts=${work}/sources.list.d"
  -o "Dir::State::Lists=${work}/lists"
  -o "Dir::State::extended_states=${work}/extended_states"
  -o "Dir::State::status=${rootfs}/var/lib/dpkg/status"
  -o "Dir::Cache=${work}/cache"
  -o "APT::Sandbox::User=root"
  -o "Debug::NoLocking=true"
)
apt-get "${apt_opts[@]}" update
apt-get "${apt_opts[@]}" install -y --download-only --no-install-recommends "${packages[@]}"

for deb in "${work}"/cache/archives/*.deb; do
  dpkg-deb -x "${deb}" "${rootfs}"
  # shellcheck disable=SC2016 # dpkg's format syntax, not the shell's
  echo "unpacked $(dpkg-deb -W --showformat '${Package} ${Version}' "${deb}")"
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

# Anything the container runtime injects into /etc must not be shadowed here.
# FEX resolves a guest path inside the RootFS first and only falls back to the
# host when it is absent, and the stock image ships these as empty files, so
# the guest reads no nameservers at all and every lookup fails with "Cannot
# resolve destination host". Deleting them is what reaches the real ones.
# nsswitch.conf stays: glibc's resolver needs it and this copy is the valid one.
rm -f "${rootfs}/etc/resolv.conf" "${rootfs}/etc/hosts"

rm -rf \
  "${rootfs}/usr/share/doc" \
  "${rootfs}/usr/share/man" \
  "${rootfs}/usr/share/locale" \
  "${rootfs}/var/lib/apt/lists" \
  "${rootfs}/var/cache"
