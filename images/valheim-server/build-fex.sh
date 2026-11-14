#!/usr/bin/env bash
# Build FEX from its release tag.
#
# Upstream releases ship source only, and the PPA that does ship binaries keeps
# just its newest build, so a pinned version there disappears within days of
# the next release. A tag cannot.
#
# One build, against an armv8.2-a baseline. The baseline only governs FEX's own
# host code. The JIT detects host features at
# runtime, so translated guest code is the same whatever this is set to.
#
# Thunks and FEXConfig are off. Thunks forward GL, Vulkan and CUDA calls to
# host libraries, which a headless server never makes, and they are what need
# the x86 cross toolchains. FEXConfig is a Qt GUI.
set -euo pipefail

version=${1:?usage: build-fex.sh FEX_VERSION DESTDIR}
destdir=${2:?usage: build-fex.sh FEX_VERSION DESTDIR}
arch=armv8.2

apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates clang cmake git lld llvm ninja-build pkgconf python3

src=$(mktemp -d)
stage=$(mktemp -d)

git clone --quiet --depth 1 --branch "FEX-${version}" \
  https://github.com/FEX-Emu/FEX.git "${src}"
# The three *-bins submodules are prebuilt test corpora, the bulk of the
# checkout, and only the test suites read them.
mapfile -t submodules < <(
  git -C "${src}" config --file .gitmodules --get-regexp '\.path$' |
    awk '$2 !~ /-bins$/ {print $2}'
)
git -C "${src}" submodule update --init --depth 1 --quiet -- "${submodules[@]}"

CC=clang CXX=clang++ cmake -S "${src}" -B "${src}/build" -G Ninja \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr \
  -DUSE_LINKER=lld \
  -DENABLE_LTO=True \
  -DBUILD_TESTING=False \
  -DENABLE_ASSERTIONS=False \
  -DBUILD_THUNKS=False \
  -DBUILD_FEXCONFIG=False \
  -DENABLE_CCACHE=False \
  -DTUNE_CPU=generic \
  -DTUNE_ARCH=armv8.2-a \
  -DOVERRIDE_VERSION="${version}"
time cmake --build "${src}/build"
DESTDIR="${stage}" cmake --install "${src}/build"

# FEXRootFSFetcher downloads and mounts squashfs images, which is what the
# prebuilt guest tree replaces.
rm -f "${stage}/usr/bin/FEXRootFSFetcher"

mkdir -p "${destdir}/${arch}"
cp -a "${stage}/usr/bin" "${destdir}/${arch}/bin"
cp -a "${stage}/usr/share/fex-emu/." /usr/share/fex-emu/

find "${stage}" -type f | sed "s|^${stage}||" | sort
