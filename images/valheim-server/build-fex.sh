#!/usr/bin/env bash
# Build FEX from its release tag, once per CPU baseline asked for.
#
# Upstream releases ship source only, and the PPA that does ship binaries keeps
# just its newest build, so a pinned version there disappears within days of
# the next release. A tag cannot.
#
# Each baseline is a full build: FEX applies -march to the whole compile, so
# one configure produces one baseline. The baseline only governs FEX's own
# host code. The JIT detects host features at runtime, so translated guest
# code is the same whatever it is set to.
#
# Thunks are built but only the VDSO thunk is shipped. FEX maps it into every
# guest whenever the file exists, thunks enabled or not, and it is what gives
# the guest's clock_gettime and gettimeofday a path that skips the syscall.
# The rest forward GL, Vulkan and CUDA calls a headless server never makes. It
# is x86 code, so it is built with the first baseline only. FEXConfig, a Qt
# GUI, is off.
set -euo pipefail

usage="usage: build-fex.sh FEX_VERSION DESTDIR ARCH..."
version=${1:?${usage}}
destdir=${2:?${usage}}
shift 2
(($# > 0)) || {
  echo "${usage}" >&2
  exit 1
}

# Limited to the baselines select-fex.sh holds HWCAP requirements for, so a
# build it could not choose between is refused here rather than at startup.
for arch in "$@"; do
  case ${arch} in
  armv8.0 | armv8.2 | armv8.4) ;;
  *)
    echo "error: unknown FEX baseline ${arch}; select-fex.sh knows armv8.0, armv8.2 and armv8.4" >&2
    exit 1
    ;;
  esac
done

# The second group is the thunk build: the generator needs libclang, LLVM's
# CMake packages and OpenSSL, the guest side cross-compiles for x86-64 and
# i686, and the host side needs the headers of every library it forwards.
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates clang clang-tools cmake git lld llvm ninja-build pkgconf \
  python3 \
  libclang-dev llvm-dev libssl-dev \
  g++-x86-64-linux-gnu libc6-dev-i386-cross libgcc-13-dev-i386-cross \
  libstdc++-13-dev-i386-cross \
  libasound2-dev libdrm-dev libegl-dev libgl-dev libwayland-dev libx11-dev \
  libxcb-dri2-0-dev libxcb-dri3-dev libxcb-glx0-dev libxcb-present-dev \
  libxcb-shm0-dev libxrandr-dev libxrender-dev libxshmfence-dev

src=$(mktemp -d)

git clone --quiet --depth 1 --branch "FEX-${version}" \
  https://github.com/FEX-Emu/FEX.git "${src}"
# The three *-bins submodules are prebuilt test corpora, the bulk of the
# checkout, and only the test suites read them.
mapfile -t submodules < <(
  git -C "${src}" config --file .gitmodules --get-regexp '\.path$' |
    awk '$2 !~ /-bins$/ {print $2}'
)
git -C "${src}" submodule update --init --depth 1 --quiet -- "${submodules[@]}"

thunks=True
for arch in "$@"; do
  build="${src}/build-${arch}"
  stage=$(mktemp -d)

  CC=clang CXX=clang++ cmake -S "${src}" -B "${build}" -G Ninja \
    -DCMAKE_BUILD_TYPE=Release \
    -DCMAKE_INSTALL_PREFIX=/usr \
    -DUSE_LINKER=lld \
    -DENABLE_LTO=True \
    -DBUILD_TESTING=False \
    -DENABLE_ASSERTIONS=False \
    -DBUILD_THUNKS="${thunks}" \
    -DBUILD_FEXCONFIG=False \
    -DENABLE_CCACHE=False \
    -DTUNE_CPU=generic \
    -DTUNE_ARCH="${arch}-a" \
    -DOVERRIDE_VERSION="${version}"
  echo "==> building FEX ${version} for ${arch}"
  time cmake --build "${build}"
  DESTDIR="${stage}" cmake --install "${build}"

  # FEXRootFSFetcher downloads and mounts squashfs images, which is what the
  # prebuilt guest tree replaces.
  rm -f "${stage}/usr/bin/FEXRootFSFetcher"

  mkdir -p "${destdir}/${arch}"
  cp -a "${stage}/usr/bin" "${destdir}/${arch}/bin"
  # AppConfig and ThunksDB are plain data, identical across baselines.
  cp -a "${stage}/usr/share/fex-emu/AppConfig" "${stage}/usr/share/fex-emu/ThunksDB.json" \
    /usr/share/fex-emu/
  if [[ ${thunks} == True ]]; then
    mkdir -p /usr/share/fex-emu/GuestThunks
    cp -a "${stage}/usr/share/fex-emu/GuestThunks/libVDSO-guest.so" \
      /usr/share/fex-emu/GuestThunks/
  fi
  thunks=False

  find "${stage}" -type f | sed "s|^${stage}||" | sort
done
