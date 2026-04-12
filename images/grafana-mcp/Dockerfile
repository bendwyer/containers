ARG OWNER=grafana
ARG BINARY=mcp-grafana

FROM alpine:3.23 AS build

ARG OWNER
ARG BINARY
ARG TARGETARCH

RUN apk add --no-cache curl tar && \
  case "${TARGETARCH}" in \
    amd64) ARCH=x86_64 ;; \
    arm64) ARCH=arm64 ;; \
    *) ARCH="${TARGETARCH}" ;; \
  esac && \
  curl -sSL "https://github.com/${OWNER}/${BINARY}/releases/latest/download/${BINARY}_Linux_${ARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["mcp-grafana"]
CMD ["--help"]
