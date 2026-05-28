#!/bin/bash

# ==============================================================================
# Failsafe Automated Git Backup Daemon
# ==============================================================================
# Parameters
INTERVAL=${1:-30}
BRANCH=$(git branch --show-current)
BRANCH=${BRANCH:-main}
REPO_DIR=$(pwd)

echo "🚀 Starting Git Failsafe Daemon in $REPO_DIR"
echo "🕒 Interval: ${INTERVAL}s | 🌿 Branch: $BRANCH"

while true; do
    # 1. Check for changes (including untracked files)
    if [[ -n $(git status -s) ]]; then
        echo "📝 Changes detected. Starting sync cycle..."

        # Stage all changes
        git add .

        # 2. Generate Contextual Commit Message via AI
        DIFF=$(git diff --staged)
        
        # Check for available AI CLI
        AI_BIN=""
        if command -v agy >/dev/null 2>&1; then
            AI_BIN="agy"
        elif command -v gemini >/dev/null 2>&1; then
            AI_BIN="gemini"
        fi

        COMMIT_MSG=""
        if [[ -n "$AI_BIN" ]]; then
            echo "🤖 Generating commit message via $AI_BIN..."
            PROMPT="Review this git diff and write a concise, one-sentence Conventional Commit message. Output ONLY the raw text. Do not wrap it in markdown blockquotes or backticks."
            
            # Pipe diff to AI CLI
            COMMIT_MSG=$(echo "$DIFF" | "$AI_BIN" -p "$PROMPT" 2>/dev/null | tr -d '\n' | sed 's/^ *//;s/ *$//')
        fi

        # 3. Resiliency Fallback
        if [[ -z "$COMMIT_MSG" ]]; then
            TIMESTAMP=$(date +"%Y-%m-%d %H:%M:%S")
            COMMIT_MSG="auto: periodic safety sync $TIMESTAMP"
            echo "🔌 AI Offline or empty response. Using fallback: $COMMIT_MSG"
        else
            echo "✅ AI Generated Message: $COMMIT_MSG"
        fi

        # 4. Commit locally
        if git commit -m "$COMMIT_MSG"; then
            echo "💾 Local commit created."
        else
            echo "ℹ️ Nothing to commit."
        fi

        # 5. Pull remote changes to prevent conflicts
        echo "📥 Pulling latest from origin $BRANCH..."
        if ! git pull origin "$BRANCH" --rebase; then
            echo "⚠️ Rebase conflict detected! Aborting rebase to keep local state clean."
            git rebase --abort
            echo "⏭️ Skipping push this cycle to allow manual resolution if needed."
            sleep "$INTERVAL"
            continue
        fi

        # 6. Push to remote
        echo "📤 Pushing to origin $BRANCH..."
        if ! git push origin "$BRANCH"; then
            echo "❌ Push failed (Network offline or remote rejected). Keeping local commit."
            echo "🔄 Will retry push on next cycle."
        else
            echo "✨ Sync complete!"
        fi
    fi

    sleep "$INTERVAL"
done
