#!/usr/bin/env bash
set -euo pipefail

repo_owner="hashicorp"
repo_name="terraform-mcp-server"

# Unauthenticated api.github.com allows 60 requests/hour per IP, and hosted
# runners share IPs. CI exports GH_TOKEN for the repo's 1000/hour; it is
# unset for local runs, which stay unauthenticated.
auth=()
if [[ -n "${GH_TOKEN:-}" ]]; then
  auth=(-H "Authorization: Bearer ${GH_TOKEN}")
fi

curl -fsSL --retry 3 "${auth[@]}" \
  "https://api.github.com/repos/${repo_owner}/${repo_name}/releases/latest" |
  jq -r '.tag_name | split("v")[1]'
