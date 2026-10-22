FROM golang:1.27-alpine3.24@sha256:b8c9b82affbbb82a020bbcf998dad2ba9516944ac7e883524642a481a9505e9c AS build
ARG VERSION
RUN apk add --no-cache curl tar && \
  repo_owner="smbl64" \
  repo_name="humble-cli" \
  arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') && \
  curl -sSL https://github.com/${repo_owner}/${repo_name}/releases/download/v${VERSION}/humble-cli-linux-${arch}.tar.gz | tar xzf - -C /usr/local/bin/ humble-cli-linux-${arch} && \
  mv /usr/local/bin/humble-cli-linux-${arch} /usr/local/bin/humble-cli
RUN ls -la /usr/local/bin

FROM alpine:3.24@sha256:71e9b7bae160ba167fa17414401b94cbcdaf3c32feeab9298bd794a2a0614c35
RUN apk add --no-cache libc6-compat
COPY --from=build /usr/local/bin/humble-cli /usr/local/bin/humble-cli
ENTRYPOINT ["humble-cli"]
CMD [ "--help" ]
