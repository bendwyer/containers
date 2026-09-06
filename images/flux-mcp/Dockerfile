ARG OWNER=controlplaneio-fluxcd
ARG BINARY=flux-operator-mcp
ARG REPOSITORY=flux-operator

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS build

ARG OWNER
ARG BINARY
ARG REPOSITORY
ARG TARGETARCH

RUN apk add --no-cache curl jq tar && \
  RELEASE=$(curl -sSL https://api.github.com/repos/${OWNER}/${REPOSITORY}/releases/latest | jq -r '.tag_name | split("v")[1]') && \
  curl -sSL "https://github.com/${OWNER}/${REPOSITORY}/releases/latest/download/${BINARY}_${RELEASE}_linux_${TARGETARCH}.tar.gz" \
    | tar xzf - -C /usr/local/bin/ "${BINARY}"

FROM gcr.io/distroless/static-debian13@sha256:f2ea2709ac8db56323cbd7d014277f32cb572d9ea124b0076f7aafe5980678fe

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["flux-operator-mcp"]
CMD ["--help"]
