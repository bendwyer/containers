Flux MCP Server
===============

[GitHub](https://github.com/controlplaneio-fluxcd/flux-operator)
[Docs](https://fluxoperator.dev/docs/mcp/install/)

ControlPlane provides an image, `ghcr.io/controlplaneio-fluxcd/flux-operator-mcp` which is exactly the same size as the distroless build, ~47 MB.

Claude Code
------

```json
{
  "mcpServers": {
    "flux": {
      "type": "stdio",
      "command": "docker",
      "args": [
        "run",
        "-i",        
        "--rm",
        "--init",
        "-v", "/path/to/.kube:/root/.kube:ro",
        "-e", "KUBECONFIG=/root/.kube/config",        
        "ghcr.io/bendwyer/containers/flux-mcp:0"
        "serve"
      ]
    }
  }
}
```
