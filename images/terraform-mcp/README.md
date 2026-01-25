Terraform MCP Server
====================

[GitHub](https://github.com/hashicorp/terraform-mcp-server)\
[Docs](https://developer.hashicorp.com/terraform/docs/tools/mcp-server)

`hashicorp/terraform-mcp-server` is already distroless and very small (~10 MB). Using the included Dockerfile, the distroless image is slightly bigger (~12 MB).

Claude
------

```json
{
  "mcpServers": {
    "terraform": {
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "ghcr.io/bendwyer/containers/terraform-mcp:0"
      ]
    }
  }
}
```
