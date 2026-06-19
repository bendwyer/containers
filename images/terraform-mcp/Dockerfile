ARG OWNER=hashicorp
ARG BINARY=terraform-mcp-server

FROM alpine:3.24 AS build

ARG OWNER
ARG BINARY
ARG TARGETARCH

RUN apk add --no-cache curl jq unzip && \
  RELEASE=$(curl -sSL "https://api.github.com/repos/${OWNER}/${BINARY}/releases/latest" | jq -r '.tag_name | split("v")[1]') && \
  curl -sSLO "https://releases.hashicorp.com/${BINARY}/${RELEASE}/${BINARY}_${RELEASE}_linux_${TARGETARCH}.zip" && \
  unzip -jq "${BINARY}_${RELEASE}_linux_${TARGETARCH}.zip" "${BINARY}" -d /usr/local/bin/

FROM gcr.io/distroless/static-debian13

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["terraform-mcp-server"]
CMD ["--help"]
