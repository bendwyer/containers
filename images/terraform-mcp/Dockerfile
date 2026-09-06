ARG OWNER=hashicorp
ARG BINARY=terraform-mcp-server

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b AS build

ARG OWNER
ARG BINARY
ARG TARGETARCH
ARG VERSION

RUN apk add --no-cache curl unzip && \
  curl -sSLO "https://releases.hashicorp.com/${BINARY}/${VERSION}/${BINARY}_${VERSION}_linux_${TARGETARCH}.zip" && \
  unzip -jq "${BINARY}_${VERSION}_linux_${TARGETARCH}.zip" "${BINARY}" -d /usr/local/bin/

FROM gcr.io/distroless/static-debian13@sha256:f2ea2709ac8db56323cbd7d014277f32cb572d9ea124b0076f7aafe5980678fe

ARG BINARY

COPY --from=build /usr/local/bin/${BINARY} /usr/local/bin/${BINARY}
ENTRYPOINT ["terraform-mcp-server"]
CMD ["--help"]
