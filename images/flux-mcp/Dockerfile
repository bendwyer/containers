ARG OWNER=controlplaneio-fluxcd
ARG BINARY=flux-operator-mcp
ARG REPOSITORY=flux-operator

FROM alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a AS build

ARG OWNER
ARG BINARY
ARG REPOSITORY
ARG TARGETARCH
ARG VERSION

RUN apk add --no-cache curl tar && \
  curl -sSL "https://github.com/${OWNER}/${REPOSITORY}/releases/download/v${VERSION}/${BINARY}_${VERSION}_linux_${TARGETARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13@sha256:15f8637509792e05b97d83451b0b6ccfa4abd548f997e406ac1e72c87fe1fa78

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["flux-operator-mcp"]
CMD ["--help"]
