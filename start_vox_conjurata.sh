#!/bin/bash
cd /var/home/EvokeStudio/vox-conjurata
notify-send "Vox Conjurata" "Starting AI stack and model servers..." --icon=utilities-system-monitor
podman compose up -d
notify-send "Vox Conjurata" "AI stack started successfully." --icon=utilities-system-monitor
