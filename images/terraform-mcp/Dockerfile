ARG OWNER=hashicorp
ARG BINARY=terraform-mcp-server

FROM alpine:3.24@sha256:294b683cb724975bec92580e1e685676bd4b50bda910ddb8c51d4cabeaec77e6 AS build

ARG OWNER
ARG BINARY
ARG TARGETARCH
ARG VERSION

RUN apk add --no-cache curl unzip && \
  curl -sSLO "https://releases.hashicorp.com/${BINARY}/${VERSION}/${BINARY}_${VERSION}_linux_${TARGETARCH}.zip" && \
  unzip -jq "${BINARY}_${VERSION}_linux_${TARGETARCH}.zip" "${BINARY}" -d /usr/local/bin/

FROM gcr.io/distroless/static-debian13@sha256:58133991db06659feaabe0f4e97a35cebf15ef4ea08f8a4c6d2ee5f75e4aa6a0

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["terraform-mcp-server"]
CMD ["--help"]
