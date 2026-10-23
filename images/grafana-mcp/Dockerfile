ARG OWNER=grafana
ARG BINARY=mcp-grafana

FROM alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS build

ARG OWNER
ARG BINARY
ARG TARGETARCH
ARG VERSION

RUN apk add --no-cache curl tar && \
  case "${TARGETARCH}" in \
    amd64) ARCH=x86_64 ;; \
    arm64) ARCH=arm64 ;; \
    *) ARCH="${TARGETARCH}" ;; \
  esac && \
  curl -sSL "https://github.com/${OWNER}/${BINARY}/releases/download/v${VERSION}/${BINARY}_Linux_${ARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13@sha256:034df5c1737e8241f6ed6423a12e720ee0f6d6f088609ee172f98f6f2166ef5d

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["mcp-grafana"]
CMD ["--help"]
