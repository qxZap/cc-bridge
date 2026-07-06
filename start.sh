#!/usr/bin/env bash
# cc-bridge — leave running on the machine with your Claude Code chats.
# Open the printed http://<lan-ip>:8787/ URL on your phone over LAN/VPN.
cd "$(dirname "$0")" && exec python3 bridge.py "$@"
