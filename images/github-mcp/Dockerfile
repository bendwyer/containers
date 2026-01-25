FROM alpine:3.23 AS build

RUN apk add --no-cache curl jq tar && \
  ARCH=$(uname -m | sed 's/aarch64/arm64/') && \
  curl -sSL https://github.com/github/github-mcp-server/releases/latest/download/github-mcp-server_Linux_${ARCH}.tar.gz | tar xzf - -C /usr/local/bin/ github-mcp-server

FROM gcr.io/distroless/static-debian12
COPY --from=build /usr/local/bin/github-mcp-server /usr/local/bin/github-mcp-server
ENTRYPOINT ["github-mcp-server"]
CMD ["stdio"]
