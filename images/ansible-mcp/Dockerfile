# Upstream stopped publishing the server to npm after 26.6.0, and its own
# image is amd64-only. Every release attaches the VS Code extension, which
# carries the same bundled server, so this takes it from there.
FROM alpine:3.24@sha256:f59abe656b692d901e184be3445e084786602108599d2a51d229c0f5a48c71bf AS fetch
ARG VERSION
RUN apk add --no-cache curl unzip
RUN curl -fsSL --retry 3 -o /tmp/ansible.vsix \
      "https://github.com/ansible/vscode-ansible/releases/download/v${VERSION}/ansible-${VERSION}.vsix" && \
    unzip -q /tmp/ansible.vsix 'extension/packages/ansible-mcp-server/*' -d /tmp/vsix && \
    mv /tmp/vsix/extension/packages/ansible-mcp-server /app

# The Node line is part of the image name, so Renovate refreshes the digest but
# never moves the line; follow the current Node LTS line by hand.
FROM gcr.io/distroless/nodejs26-debian13@sha256:80631bd2f0e7ef192138a7409013b47d92fd11b8ff3bb35f5c0a65be3ecaea4c
COPY --from=fetch /app /app
COPY check-initialize.js /tmp/check-initialize.js
WORKDIR /app
USER 65532:65532
# The bundle has shipped unable to start before (ansible/vscode-ansible#2788);
# fail the build unless the server answers an MCP initialize.
RUN ["/nodejs/bin/node", "/tmp/check-initialize.js"]
# cli.cjs is the package's bin and what upstream's own image starts.
ENTRYPOINT ["/nodejs/bin/node", "dist/cli.cjs"]
CMD ["--stdio"]
