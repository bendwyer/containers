Terraform MCP Server
====================

[GitHub](https://github.com/hashicorp/terraform-mcp-server)\
[Docs](https://developer.hashicorp.com/terraform/docs/tools/mcp-server)

Hashicorp provides an image, `hashicorp/terraform-mcp-server` is already and very small (~10 MB) as it's simply an empty container with ca-certs and the binary. This repository's distroless build is slightly bigger (~12 MB) due to the distroless image having a few Debian packages.

Claude Code
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
        "ghcr.io/bendwyer/containers/terraform-mcp:0",
        "--stdio"
      ]
    }
  }
}
```
