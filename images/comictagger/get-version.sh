#!/usr/bin/env bash
set -euo pipefail

# Derived from the pip pin, so the tag cannot claim a version the image does
# not contain. PEP440 1.6.0b9 renders as the 1.6.0-b9 tag already published.
version=$(sed -n 's/^comictagger\[.*\]==\(.*\)$/\1/p' requirements.txt)
if [[ -z "$version" ]]; then
  echo "no comictagger pin found in requirements.txt" >&2
  exit 1
fi

sed -E 's/([0-9])(a|b|rc)/\1-\2/' <<< "$version"
