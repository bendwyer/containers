#!/usr/bin/env bash

# First-party: the image is a composition of FEX, a guest library tree and an
# entrypoint, with no single upstream whose version describes it. The game is
# not baked in, so its version cannot stand in either. Bump on every change.
echo "1.0.1"
