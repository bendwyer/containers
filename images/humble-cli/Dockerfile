FROM golang:1.27-alpine3.24@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS build
ARG VERSION
RUN apk add --no-cache curl tar && \
  repo_owner="smbl64" \
  repo_name="humble-cli" \
  arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') && \
  curl -sSL https://github.com/${repo_owner}/${repo_name}/releases/download/v${VERSION}/humble-cli-linux-${arch}.tar.gz | tar xzf - -C /usr/local/bin/ humble-cli-linux-${arch} && \
  mv /usr/local/bin/humble-cli-linux-${arch} /usr/local/bin/humble-cli
RUN ls -la /usr/local/bin

FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf
RUN apk add --no-cache libc6-compat
COPY --from=build /usr/local/bin/humble-cli /usr/local/bin/humble-cli
ENTRYPOINT ["humble-cli"]
CMD [ "--help" ]
