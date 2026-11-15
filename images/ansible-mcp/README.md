Ansible MCP Server
===================

[Docs](https://docs.ansible.com/projects/vscode-ansible/mcp/)\
[GitHub](https://github.com/ansible/vscode-ansible)\
[npm](https://www.npmjs.com/package/@ansible/ansible-mcp-server)

Upstream's own image, `ghcr.io/ansible/devtools-mcp-server`, is amd64-only,
and npm stopped at 26.6.0. This image takes the server from the VS Code
extension each release attaches, for amd64 and arm64, and runs as a non-root
user.

Versions are calendar-based (26.11.0 is November 2026), so the `26` tag only
follows releases from 2026. Pin a full version.

Claude
------

```json
{
  "mcpServers": {
    "ansible": {
      "type": "stdio",
      "command": "docker",
      "args":[
        "run",
        "-i",
        "--rm",
        "ghcr.io/bendwyer/containers/ansible-mcp:26.11.0",
        "--stdio"
      ]
    }
  }
}
```
