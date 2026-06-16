#!/bin/bash

# Corporate Proxy Bypass Configuration
export NO_PROXY="localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,5.104.85.38"
export no_proxy="localhost,127.0.0.1,192.168.0.0/16,10.0.0.0/8,172.16.0.0/12,5.104.85.38"

# Default Configuration
DEFAULT_SERVER_URL="http://localhost:8080"
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
else
    if [ "$SYNC_MEMORIES" = "y" ] || [ "$SYNC_MEMORIES" = "true" ] || [ "$SYNC_MEMORIES" = "1" ] || [ "$SYNC_MEMORIES" = "--sync-memories" ]; then
        SYNC_MEMORIES="--sync-memories"
    else
        SYNC_MEMORIES=""
    fi
fi

# 6. Upload Type (JSON vs DB File)
UPLOAD_DB=$6
if [ -z "$UPLOAD_DB" ]; then
    read -p "Use SQLite database file upload instead of JSON payload (bypasses WAF blocks)? (y/n) [n]: " DB_ANS
    DB_ANS=${DB_ANS:-"n"}
    if [[ "$DB_ANS" =~ ^[Yy]$ ]]; then
        UPLOAD_DB="--upload-db"
    else
        UPLOAD_DB=""
    fi
else
    if [ "$UPLOAD_DB" = "y" ] || [ "$UPLOAD_DB" = "true" ] || [ "$UPLOAD_DB" = "1" ] || [ "$UPLOAD_DB" = "--upload-db" ]; then
        UPLOAD_DB="--upload-db"
    else
        UPLOAD_DB=""
    fi
fi

# 7. Pure Python Parser Toggle
USE_PURE_PYTHON=$7
if [ -z "$USE_PURE_PYTHON" ]; then
    read -p "Use pure Python codebase scanner instead of native codegraph CLI? (y/n) [y]: " PY_ANS
    PY_ANS=${PY_ANS:-"y"}
    if [[ "$PY_ANS" =~ ^[Yy]$ ]]; then
        export PB_USE_PURE_PYTHON_PARSER="true"
    else
        export PB_USE_PURE_PYTHON_PARSER="false"
    fi
else
    if [ "$USE_PURE_PYTHON" = "true" ] || [ "$USE_PURE_PYTHON" = "1" ] || [ "$USE_PURE_PYTHON" = "y" ]; then
        export PB_USE_PURE_PYTHON_PARSER="true"
    else
        export PB_USE_PURE_PYTHON_PARSER="false"
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
echo "Upload DB:    ${UPLOAD_DB:-no}"
echo "Pure Python:  $PB_USE_PURE_PYTHON_PARSER"
echo "-------------------"
echo "Running sync..."
echo ""

# Run sync command
python3 -m projectbrain.main codegraph-sync "$PROJECT_ID" "$SERVER_URL" "$ABS_PROJECT_PATH" "$BRANCH" $SYNC_MEMORIES $UPLOAD_DB
