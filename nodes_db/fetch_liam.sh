#!/bin/bash
# Download nodes.json next to this script, regardless of the current directory
cd "$(dirname "$0")" || exit 1
wget -O nodes.json https://meshtastic.liamcottle.net/api/v1/nodes
