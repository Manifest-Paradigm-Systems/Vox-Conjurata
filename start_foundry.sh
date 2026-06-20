#!/bin/bash
# Start foundry-vtt and caddy if they are not already running
if ! podman ps --format "{{.Names}}" | grep -q "foundry-vtt"; then
    cd /var/home/EvokeStudio/vox-conjurata
    notify-send "Foundry VTT" "Starting Foundry VTT container..." --icon=applications-games
    podman compose up -d foundry-vtt caddy
fi
xdg-open http://localhost:30000
