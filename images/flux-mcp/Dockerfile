FROM alpine:3.23 AS build

RUN apk add --no-cache curl jq tar && \
  ARCH=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') && \
  RELEASE=$(curl -sSL https://api.github.com/repos/controlplaneio-fluxcd/flux-operator/releases/latest | jq -r '.tag_name | split("v")[1]') && \
  curl -sSL https://github.com/controlplaneio-fluxcd/flux-operator/releases/latest/download/flux-operator-mcp_${RELEASE}_linux_${ARCH}.tar.gz | tar xzf - -C /usr/local/bin/ flux-operator-mcp

FROM gcr.io/distroless/static-debian12
COPY --from=build /usr/local/bin/flux-operator-mcp /usr/local/bin/flux-operator-mcp
ENTRYPOINT ["flux-operator-mcp"]
CMD ["serve"]
