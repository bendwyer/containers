#!/usr/bin/env bash

package_owner="ansible"
package_name="ansible-mcp-server"

curl -sSL https://registry.npmjs.org/@${package_owner}/${package_name}/latest | jq -r '.version'
