#!/usr/bin/env bash

repo_owner="grafana"
repo_name="mcp-grafana"

curl -sSL https://api.github.com/repos/${repo_owner}/${repo_name}/releases/latest | jq -r '.tag_name | split("v")[1]'
