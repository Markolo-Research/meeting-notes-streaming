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

# Read status file
if [ -f "$STATUS_FILE" ]; then
    # shellcheck disable=SC1090
    source "$STATUS_FILE"
    
    case "${STATUS:-idle}" in
        "recording")
            if [ "$APP_RUNNING" -eq 1 ]; then
                echo "{\"text\": \"󰦕 ${DURATION:-00:00}\", \"tooltip\": \"Recording: ${TITLE:-Meeting}\", \"class\": \"recording\"}"
            else
                echo '{"text": "󰗠", "tooltip": "Meeting Notes (not running)", "class": "idle"}'
            fi
            ;;
        "processing")
            if [ "$APP_RUNNING" -eq 1 ]; then
                echo "{\"text\": \"󰄬\", \"tooltip\": \"Processing recording...\", \"class\": \"processing\"}"
            else
                echo '{"text": "󰗠", "tooltip": "Meeting Notes (not running)", "class": "idle"}'
            fi
            ;;
        *)
            if [ "$APP_RUNNING" -eq 1 ]; then
                echo "{\"text\": \"󰗠\", \"tooltip\": \"Meeting Notes (ready)\", \"class\": \"ready\"}"
            else
                echo '{"text": "󰗠", "tooltip": "Meeting Notes (not running)", "class": "idle"}'
            fi
            ;;
    esac
else
    if [ "$APP_RUNNING" -eq 1 ]; then
        echo '{"text": "󰗠", "tooltip": "Meeting Notes (ready)", "class": "ready"}'
    else
        echo '{"text": "󰗠", "tooltip": "Meeting Notes (not running)", "class": "idle"}'
    fi
fi
