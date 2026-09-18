ARG OWNER=grafana
ARG BINARY=mcp-grafana

FROM alpine:3.24@sha256:5b02b42e375f7426f8d65c3af331ca05d9878f9989230354504e0b9dfd431f60 AS build

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

FROM gcr.io/distroless/static-debian13@sha256:f2ea2709ac8db56323cbd7d014277f32cb572d9ea124b0076f7aafe5980678fe

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["mcp-grafana"]
CMD ["--help"]
