Grafana MCP Server
=================

[GitHub / Docs](https://github.com/grafana/mcp-grafana)

Grafana provides a docker image, `docker.io/grafana/mcp-grafana`, but it is around ~46 MB in size. A distroless build is around one-third the size at ~15 MB.

Claude Code
------

```json
{
  "mcpServers": {
    "grafana": {
      "type": "stdio",
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "GRAFANA_URL",
        "-e", "GRAFANA_SERVICE_ACCOUNT_TOKEN",
        "ghcr.io/bendwyer/containers/grafana-mcp:0"
      ],
      "env": {
        "GRAFANA_URL": "${GRAFANA_URL}",
        "GRAFANA_SERVICE_ACCOUNT_TOKEN": "${GRAFANA_SERVICE_ACCOUNT_TOKEN}"
      }
    }
  }
}
```
