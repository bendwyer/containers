ARG OWNER=controlplaneio-fluxcd
ARG BINARY=flux-operator-mcp
ARG REPOSITORY=flux-operator

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS build

ARG OWNER
ARG BINARY
ARG REPOSITORY
ARG TARGETARCH
ARG VERSION

RUN apk add --no-cache curl tar && \
  curl -sSL "https://github.com/${OWNER}/${REPOSITORY}/releases/download/v${VERSION}/${BINARY}_${VERSION}_linux_${TARGETARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13@sha256:f2ea2709ac8db56323cbd7d014277f32cb572d9ea124b0076f7aafe5980678fe

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["flux-operator-mcp"]
CMD ["--help"]
