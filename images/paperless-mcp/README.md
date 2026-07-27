Paperless MCP Server
====================

[GitHub / Docs](https://github.com/barryw/PaperlessMCP)

Upstream publishes `ghcr.io/barryw/paperlessmcp`, but it is built `linux/amd64`
only. Upstream also ships no release binaries, so this builds from the tagged
source rather than repackaging one.

The runtime is Microsoft's chiseled base, the distroless equivalent for .NET: no
shell, no package manager, non-root by default. `distroless/static` cannot be
used because a framework-dependent .NET build needs the runtime present.

Serves Streamable HTTP at `POST /mcp` on port 5000. Sessions are stateful, so
the server issues an `Mcp-Session-Id` and expects it returned on later requests.

```sh
docker run --rm -p 5000:5000 \
  -e PAPERLESS_BASE_URL \
  -e PAPERLESS_API_TOKEN \
  ghcr.io/bendwyer/containers/paperless-mcp:0
```
