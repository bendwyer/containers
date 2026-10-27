FROM golang:1.27-alpine3.24@sha256:d200d2dd76b03a99b52651fd1325aff7dcd35422c35c01bac56571b0507f3deb AS build
ARG VERSION
RUN apk add --no-cache curl tar && \
  repo_owner="smbl64" \
  repo_name="humble-cli" \
  arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') && \
  curl -sSL https://github.com/${repo_owner}/${repo_name}/releases/download/v${VERSION}/humble-cli-linux-${arch}.tar.gz | tar xzf - -C /usr/local/bin/ humble-cli-linux-${arch} && \
  mv /usr/local/bin/humble-cli-linux-${arch} /usr/local/bin/humble-cli
RUN ls -la /usr/local/bin

FROM alpine:3.24@sha256:be2656061b536c1883c4034790e21d0a09c5aaaf51968b89368a3c0371ea2c9a
RUN apk add --no-cache libc6-compat
COPY --from=build /usr/local/bin/humble-cli /usr/local/bin/humble-cli
ENTRYPOINT ["humble-cli"]
CMD [ "--help" ]
