#!/usr/bin/env bash

repo_owner="barryw"
repo_name="PaperlessMCP"

curl -sSL https://api.github.com/repos/${repo_owner}/${repo_name}/releases/latest | jq -r '.tag_name | split("v")[1]'
