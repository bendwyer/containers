FROM alpine:3.23 AS build

RUN apk add --no-cache curl jq unzip && \
  ARCH=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') && \
  RELEASE=$(curl -sSL https://api.github.com/repos/hashicorp/terraform-mcp-server/releases/latest | jq -r '.tag_name | split("v")[1]') && \
  curl -sSLO https://releases.hashicorp.com/terraform-mcp-server/${RELEASE}/terraform-mcp-server_${RELEASE}_linux_${ARCH}.zip && unzip -jq terraform-mcp-server_${RELEASE}_linux_${ARCH}.zip terraform-mcp-server -d /usr/local/bin/

FROM gcr.io/distroless/static-debian12
COPY --from=build /usr/local/bin/terraform-mcp-server /usr/local/bin/terraform-mcp-server
ENTRYPOINT ["terraform-mcp-server"]
CMD ["stdio"]
