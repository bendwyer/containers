#!/usr/bin/env bash
set -euo pipefail

package_owner="ansible"
package_name="ansible-mcp-server"

curl -fsSL --retry 3 \
  "https://registry.npmjs.org/@${package_owner}/${package_name}/latest" |
  jq -r '.version'
