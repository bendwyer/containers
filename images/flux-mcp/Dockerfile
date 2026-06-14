ARG OWNER=controlplaneio-fluxcd
ARG BINARY=flux-operator-mcp
ARG REPOSITORY=flux-operator

FROM alpine:3.24 AS build

ARG OWNER
ARG BINARY
ARG REPOSITORY
ARG TARGETARCH

RUN apk add --no-cache curl jq tar && \
  RELEASE=$(curl -sSL https://api.github.com/repos/${OWNER}/${REPOSITORY}/releases/latest | jq -r '.tag_name | split("v")[1]') && \
  curl -sSL "https://github.com/${OWNER}/${REPOSITORY}/releases/latest/download/${BINARY}_${RELEASE}_linux_${TARGETARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["flux-operator-mcp"]
CMD ["--help"]
