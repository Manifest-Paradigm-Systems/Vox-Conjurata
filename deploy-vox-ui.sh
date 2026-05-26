#!/bin/bash

# Target the actual verified modules path
MODULE_BASE="$HOME/manifest-paradigm/foundry/data/Data/modules"
LOCAL_MODULE_PATH="/var/home/EvokeStudio/vox-conjurata/foundry-modules/vox-conjurata"

# 1. Copy to the project's own module folder
if [ -d "$LOCAL_MODULE_PATH" ]; then
    cp vox-interface-fix.js "$LOCAL_MODULE_PATH/"
    echo "✅ Copied to local project module: $LOCAL_MODULE_PATH"
fi

# 2. Check if we should also deploy to the active Foundry modules directory
if [ -d "$MODULE_BASE" ]; then
    # Automatically target vox-conjurata if it exists there, otherwise ask
    if [ -d "$MODULE_BASE/vox-conjurata" ]; then
        cp vox-interface-fix.js "$MODULE_BASE/vox-conjurata/"
        echo "✅ Automatically deployed to active Foundry: $MODULE_BASE/vox-conjurata"
    else
        echo "============================================================"
        echo "📦 FOUNDRY VTT CUSTOM MODULE DETECTOR"
        echo "============================================================"
        echo "Active Foundry path: $MODULE_BASE"
        # Since this is an agent environment, we won't run interactive 'select' here.
        # Instead, we list the directories for the user.
        echo "Available modules:"
        ls -1 "$MODULE_BASE"
    fi
else
    echo "⚠️ Active Foundry modules path not found at $MODULE_BASE. Skipping active deployment."
fi

echo "============================================================"
echo "🎯 DEPLOYMENT ATTEMPT COMPLETE!"
echo "💡 REMINDER: Ensure 'vox-interface-fix.js' is in your module.json!"
