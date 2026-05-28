#!/usr/bin/env bash
# Meeting Notes - Waybar Module
# Displays current meeting recording status in Waybar
set -euo pipefail

# Auto-detect meeting-notes directory (parent of hyprland/ folder where this script lives)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MEETING_NOTES_DIR="$(dirname "$SCRIPT_DIR")"
STATUS_FILE="$MEETING_NOTES_DIR/.status"
APP_PATTERN="meeting_notes\\.app|python.*run\\.py|uv run.*run\\.py"
APP_RUNNING=0
if pgrep -f "$APP_PATTERN" > /dev/null; then
    APP_RUNNING=1
fi

emit() {
    jq -cn --arg text "$1" --arg tooltip "$2" --arg class "$3" \
        '{text: $text, tooltip: $tooltip, class: $class}'
}

# Read status file
if [ -f "$STATUS_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATUS_FILE"
    
    case "${STATUS:-idle}" in
        "recording")
            if [ "$APP_RUNNING" -eq 1 ]; then
                emit "󰦕 ${DURATION:-00:00}" "Recording: ${TITLE:-Meeting}" "recording"
            else
                emit "󰗠" "Meeting Notes (not running)" "idle"
            fi
            ;;
        "processing")
            if [ "$APP_RUNNING" -eq 1 ]; then
                emit "󰄬" "Processing recording..." "processing"
            else
                emit "󰗠" "Meeting Notes (not running)" "idle"
            fi
            ;;
        *)
            if [ "$APP_RUNNING" -eq 1 ]; then
                emit "󰗠" "Meeting Notes (ready)" "ready"
            else
                emit "󰗠" "Meeting Notes (not running)" "idle"
            fi
            ;;
    esac
else
    if [ "$APP_RUNNING" -eq 1 ]; then
        emit "󰗠" "Meeting Notes (ready)" "ready"
    else
        emit "󰗠" "Meeting Notes (not running)" "idle"
    fi
fi
