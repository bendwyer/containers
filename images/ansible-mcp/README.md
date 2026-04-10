Ansible MCP Server
===================

[Docs](https://docs.ansible.com/projects/vscode-ansible/mcp/)\
[GitHub](https://github.com/ansible/vscode-ansible)\
[npm](https://www.npmjs.com/package/@ansible/ansible-mcp-server)

I was not able to find an official container for ansible-mcp-server.

Claude
------

```json
{
  "mcpServers": {
    "ansible": {
      "type": "stdio",
      "command": "docker",
      "args": [
        "run",
        "-i",
        "--rm",
        "ghcr.io/bendwyer/containers/ansible-mcp:1",
      ]
    }
  }
}
```
