# check=skip=FromPlatformFlagConstDisallowed

# Iron Gate publishes no arm64 dedicated server, so on arm64 the x86-64 binary
# runs under FEX. Both architectures load it against the same x86-64 library
# tree; only the loader differs, which keeps the amd64 image a working
# reference for everything except the emulator itself.

# Pulled for its files and never executed, so the arm64 builder needs no QEMU.
# The digest is the multi-arch index; --platform selects amd64 out of it. The
# constant is the point and the lint rule above is skipped for it: the guest is
# x86-64 whatever the build platform is, so this must not follow TARGETPLATFORM.
FROM --platform=linux/amd64 ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS guest-base

FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS guest
RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
COPY --from=guest-base / /rootfs/
COPY build-guest-rootfs.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/build-guest-rootfs.sh && build-guest-rootfs.sh /rootfs

FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS fex
ARG TARGETARCH
# renovate: datasource=github-releases depName=FEX-Emu/FEX extractVersion=^FEX-(?<version>.+)$
ARG FEX_VERSION=2609
RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates curl \
  && rm -rf /var/lib/apt/lists/*
COPY install-fex.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/install-fex.sh \
  && mkdir -p /opt/fex /usr/share/fex-emu \
  && if [ "${TARGETARCH}" = "arm64" ]; then install-fex.sh "${FEX_VERSION}" /opt/fex; fi

FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3 AS depotdownloader
ARG TARGETARCH
# renovate: datasource=github-releases depName=SteamRE/DepotDownloader extractVersion=^DepotDownloader_(?<version>.+)$
ARG DEPOTDOWNLOADER_VERSION=3.4.0
RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates curl unzip \
  && rm -rf /var/lib/apt/lists/*
# Downloading the depot with a native arm64 tool keeps the game update off the
# emulated path entirely, and avoids steamcmd, which is 32-bit x86 and would
# need a second guest tree.
RUN case "${TARGETARCH}" in \
  amd64) asset=x64 ;; \
  arm64) asset=arm64 ;; \
  *) echo "unsupported architecture: ${TARGETARCH}" >&2; exit 1 ;; \
  esac \
  && curl -fsSL --retry 3 -o /tmp/depotdownloader.zip \
  "https://github.com/SteamRE/DepotDownloader/releases/download/DepotDownloader_${DEPOTDOWNLOADER_VERSION}/DepotDownloader-linux-${asset}.zip" \
  && unzip -j /tmp/depotdownloader.zip DepotDownloader -d /usr/local/bin \
  && chmod +x /usr/local/bin/DepotDownloader \
  && /usr/local/bin/DepotDownloader -V

FROM ubuntu:24.04@sha256:008173c23f95b170204355c12626cb5a965d779a7e1283b09e9cffbb1bf33ca3
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends \
  ca-certificates \
  && rm -rf /var/lib/apt/lists/* \
  && useradd --system --uid 10001 --user-group --no-create-home valheim \
  && mkdir -p /data \
  && chown valheim:valheim /data

COPY --from=guest /rootfs /opt/valheim/rootfs
COPY --from=fex /opt/fex /opt/fex
COPY --from=fex /usr/share/fex-emu /usr/share/fex-emu
COPY --from=depotdownloader /usr/local/bin/DepotDownloader /usr/local/bin/DepotDownloader
COPY select-fex.sh entrypoint.sh /usr/local/bin/
RUN chmod +x /usr/local/bin/select-fex.sh /usr/local/bin/entrypoint.sh

# The build gate. container-build-push.yml runs no test job, so the check has
# to fail the image: on arm64 this picks a FEX build and proves it can load and
# run an x86-64 binary out of the guest tree, which is the whole premise here.
RUN test -x /opt/valheim/rootfs/lib64/ld-linux-x86-64.so.2 \
  && test -e /opt/valheim/rootfs/usr/lib/x86_64-linux-gnu/libatomic.so.1 \
  && test -e /opt/valheim/rootfs/usr/lib/x86_64-linux-gnu/libpulse.so.0 \
  && test -e /opt/valheim/rootfs/usr/lib/x86_64-linux-gnu/libpulse-mainloop-glib.so.0 \
  && test -e /opt/valheim/rootfs/usr/lib/x86_64-linux-gnu/pulseaudio/libpulsecommon-16.1.so \
  && test -s /opt/valheim/rootfs/etc/ssl/certs/ca-certificates.crt \
  && if [ "${TARGETARCH}" = "arm64" ]; then \
  FEX_ROOTFS=/opt/valheim/rootfs /usr/local/bin/select-fex.sh; \
  fi

ENV HOME=/data/home \
  FEX_ROOTFS=/opt/valheim/rootfs \
  VALHEIM_SERVER_DIR=/data/server \
  VALHEIM_SAVE_DIR=/data/worlds

# Unused on the crossplay backend, which reaches players through a relay, but
# the port still distinguishes servers sharing a public address.
EXPOSE 2456/udp 2457/udp

USER valheim
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
