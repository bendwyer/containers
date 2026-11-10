FROM golang:1.27-alpine3.24@sha256:eb61c3873c97416fff0116553fff65d67a3549f72e11d9758849a689cdd8b523 AS build
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
