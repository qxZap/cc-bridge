@echo off
REM cc-bridge — leave running on the machine with your Claude Code chats.
REM Open the printed http://<lan-ip>:8787/ URL on your phone over LAN/VPN.
REM No auth (LAN-only by design). Permission mode: bypass (runs tools without asking).
python "%~dp0bridge.py" %*
pause
