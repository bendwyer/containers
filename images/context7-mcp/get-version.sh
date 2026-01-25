#!/usr/bin/env bash

package_owner="upstash"
package_name="context7-mcp"

curl -sSL https://registry.npmjs.org/@${package_owner}/${package_name}/latest | jq -r '.version'
