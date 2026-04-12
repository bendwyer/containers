Context7 MCP Server
===================

[GitHub / Docs](https://github.com/upstash/context7)\
[npm](https://www.npmjs.com/package/@upstash/context7-mcp)

I was not able to find an official container for context7.

Claude Code
------

```json
{
  "mcpServers": {
    "context7": {
      "type": "stdio",
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "-e", "CONTEXT7_API_KEY",
        "ghcr.io/bendwyer/containers/context7-mcp:2",
        "--transport",
        "stdio"
      ],
      "env": {
        "CONTEXT7_API_KEY": "${CONTEXT7_API_KEY}"
      }
    }
  }
}
```
