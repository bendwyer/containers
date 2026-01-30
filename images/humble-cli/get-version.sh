#!/usr/bin/env bash

repo_owner="smbl64"
repo_name="humble-cli"

curl -sSL https://api.github.com/repos/${repo_owner}/${repo_name}/releases/latest | jq -r '.tag_name | split("v")[1]'
