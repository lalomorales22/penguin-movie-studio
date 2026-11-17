#!/bin/bash
# Double-click this file to start Penguin's Storyboard. It opens in your browser at http://127.0.0.1:8777
# It starts the picture/video engine (ComfyUI) by itself when it is needed.
# To stop it: click this Terminal window and press Ctrl+C (or just close the window).
# For Night mode, leave this window open and the Mac switched on. The screen may go dark; that is fine.
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
URL="http://127.0.0.1:8777"

if curl -s -o /dev/null -m 2 "$URL/api/state"; then
  echo "Storyboard is already running - opening it in your browser."
  open "$URL"
  exit 0
fi

# On a new computer, install what the app needs first (the engine, ffmpeg, LilyPond, Ollama).
if [ ! -x ComfyUI/.venv/bin/python ]; then
  echo "This looks like the first start on this computer. Running setup first..."
  STORYBOARD_FROM_START=1 /bin/bash ./setup.command
  if [ ! -x ComfyUI/.venv/bin/python ]; then
    echo "Setup did not finish. Fix what it reported, then double-click this file again."
    read -r -p "Press Return to close." _
    exit 1
  fi
fi

echo "Starting Penguin's Storyboard... your browser will open in a moment."
exec ComfyUI/.venv/bin/python storyboard/app.py
