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

FROM gcr.io/distroless/static-debian13@sha256:034df5c1737e8241f6ed6423a12e720ee0f6d6f088609ee172f98f6f2166ef5d

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["flux-operator-mcp"]
CMD ["--help"]
