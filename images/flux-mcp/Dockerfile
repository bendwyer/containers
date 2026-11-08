ARG OWNER=controlplaneio-fluxcd
ARG BINARY=flux-operator-mcp
ARG REPOSITORY=flux-operator

FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS build

ARG OWNER
ARG BINARY
ARG REPOSITORY
ARG TARGETARCH
ARG VERSION

RUN apk add --no-cache curl tar && \
  curl -sSL "https://github.com/${OWNER}/${REPOSITORY}/releases/download/v${VERSION}/${BINARY}_${VERSION}_linux_${TARGETARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13@sha256:448810357cc8e67b7208988abdba0f5ec3d3aebee0a5b13eb624979792cffc0f

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["flux-operator-mcp"]
CMD ["--help"]
