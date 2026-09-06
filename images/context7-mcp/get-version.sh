#!/usr/bin/env bash
set -euo pipefail

package_owner="upstash"
package_name="context7-mcp"

curl -fsSL --retry 3 \
  "https://registry.npmjs.org/@${package_owner}/${package_name}/latest" |
  jq -r '.version'
