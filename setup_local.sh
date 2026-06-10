#!/usr/bin/env bash
# Setup script for local AI (Ollama)

set -euo pipefail

echo "🎙️  Meeting Notes AI - Setup"
echo "=============================="
echo ""

if ! command -v uv &> /dev/null; then
    echo "❌ uv not found. Install it first:"
    echo "   https://docs.astral.sh/uv/getting-started/installation/"
    exit 1
fi

# Check system dependencies
echo "🔍 Checking system dependencies..."

if ! command -v pactl &> /dev/null; then
    echo "❌ pactl not found. Please install pulseaudio-utils:"
    echo "   sudo pacman -S pulseaudio-utils"
    exit 1
fi

if ! command -v ffmpeg &> /dev/null; then
    echo "❌ ffmpeg not found. Please install it:"
    echo "   sudo pacman -S ffmpeg"
    exit 1
fi

echo "✅ System dependencies OK"
echo ""

# Install Python dependencies
echo "📥 Installing Python dependencies..."
UV_NO_EXCLUDE_NEWER=1 uv sync --frozen --extra cloud --group dev

echo ""
echo "✅ Setup complete!"
echo ""
echo "🚀 To run the application:"
echo "   uv run meeting-notes"
echo ""
echo "📚 Read README.md for usage instructions"
echo ""
echo "⚠️  Note: First transcription will download Whisper base model (~140MB)"
