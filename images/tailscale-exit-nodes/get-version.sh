#!/usr/bin/env bash

# Hand-bumped SemVer; container-build-push.yml tags version/major.minor/major.
# Stays on 0.x through build-out; promote to 1.0.0 only after a working
# end-to-end deployment (AWS + first real Vultr) is confirmed.
echo "0.1.0"
