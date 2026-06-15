#!/bin/bash

# Default Configuration
DEFAULT_SERVER_URL="http://5.104.85.38:8080"
DEFAULT_PROJECT_PATH="."

echo "============================================="
echo "  ProjectBrain Codegraph Synchronization CLI"
echo "============================================="

# Ensure python3 is available
if ! command -v python3 &> /dev/null; then
    echo "Error: python3 is not installed or not in PATH."
    exit 1
fi

# 1. Project ID
PROJECT_ID=$1
if [ -z "$PROJECT_ID" ]; then
    read -p "Enter Project ID: " PROJECT_ID
    if [ -z "$PROJECT_ID" ]; then
        echo "Error: Project ID is required."
        exit 1
    fi
fi

# 2. Server URL
SERVER_URL=$2
if [ -z "$SERVER_URL" ]; then
    read -p "Enter Server URL [$DEFAULT_SERVER_URL]: " SERVER_URL
    SERVER_URL=${SERVER_URL:-$DEFAULT_SERVER_URL}
fi

# 3. Project Path
PROJECT_PATH=$3
if [ -z "$PROJECT_PATH" ]; then
    read -p "Enter Local Project Path [$DEFAULT_PROJECT_PATH]: " PROJECT_PATH
    PROJECT_PATH=${PROJECT_PATH:-$DEFAULT_PROJECT_PATH}
fi

# Resolve absolute path
ABS_PROJECT_PATH=$(cd "$PROJECT_PATH" && pwd)
if [ ! -d "$ABS_PROJECT_PATH" ]; then
    echo "Error: Path '$PROJECT_PATH' does not exist or is not a directory."
    exit 1
fi

# 4. Git Branch Detection
BRANCH=$4
if [ -z "$BRANCH" ]; then
    if [ -d "$ABS_PROJECT_PATH/.git" ]; then
        DETECTED_BRANCH=$(git -C "$ABS_PROJECT_PATH" rev-parse --abbrev-ref HEAD 2>/dev/null)
    fi
    DETECTED_BRANCH=${DETECTED_BRANCH:-"main"}
    read -p "Enter Git Branch [$DETECTED_BRANCH]: " BRANCH
    BRANCH=${BRANCH:-$DETECTED_BRANCH}
fi

# 5. Sync Memories (RAG file contents)
SYNC_MEMORIES=$5
if [ -z "$SYNC_MEMORIES" ]; then
    read -p "Sync codebase files as memories to RAG? (y/n) [y]: " SYNC_ANS
    SYNC_ANS=${SYNC_ANS:-"y"}
    if [[ "$SYNC_ANS" =~ ^[Yy]$ ]]; then
        SYNC_MEMORIES="--sync-memories"
    else
        SYNC_MEMORIES=""
    fi
fi

echo ""
echo "Sync Configuration:"
echo "-------------------"
echo "Project ID:   $PROJECT_ID"
echo "Server URL:   $SERVER_URL"
echo "Project Path: $ABS_PROJECT_PATH"
echo "Branch:       $BRANCH"
echo "Sync Files:   ${SYNC_MEMORIES:-no}"
echo "-------------------"
echo "Running sync..."
echo ""

# Run sync command
python3 -m projectbrain.main codegraph-sync "$PROJECT_ID" "$SERVER_URL" "$ABS_PROJECT_PATH" "$BRANCH" $SYNC_MEMORIES
