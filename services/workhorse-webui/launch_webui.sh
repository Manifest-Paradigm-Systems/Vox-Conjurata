#!/bin/bash
cd /var/home/EvokeStudio/vox-conjurata/services/workhorse-webui

# Check if the service is already running on port 8090
if ! netstat -tuln | grep -q ":8090 "; then
    # Start the service in the background
    nohup uvicorn app:app --host 0.0.0.0 --port 8090 > webui.log 2>&1 &
    # Give it a moment to initialize and bind the port
    sleep 2
fi

# Open the default web browser to the control panel
xdg-open http://localhost:8090
