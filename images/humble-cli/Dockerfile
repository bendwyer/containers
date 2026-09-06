FROM golang:1.27-alpine3.24@sha256:cf6fca6641884b8433441b2b0652976f975e1d0fdd26d177eaaf8596087f3125 AS build
RUN apk add --no-cache curl jq tar && \
  repo_owner="smbl64" \
  repo_name="humble-cli" \
  arch=$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/') && \
  curl -sSL https://github.com/${repo_owner}/${repo_name}/releases/latest/download/humble-cli-linux-${arch}.tar.gz | tar xzf - -C /usr/local/bin/ humble-cli-linux-${arch} && \
  mv /usr/local/bin/humble-cli-linux-${arch} /usr/local/bin/humble-cli
RUN ls -la /usr/local/bin

FROM alpine:3.24@sha256:28bd5fe8b56d1bd048e5babf5b10710ebe0bae67db86916198a6eec434943f8b
RUN apk add --no-cache libc6-compat
COPY --from=build /usr/local/bin/humble-cli /usr/local/bin/humble-cli
ENTRYPOINT ["humble-cli"]
CMD [ "--help" ]
