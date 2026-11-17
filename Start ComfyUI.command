#!/bin/bash
# Double-click this file to start ComfyUI. It opens in your browser at http://127.0.0.1:8188
# To stop ComfyUI: click this Terminal window and press Ctrl+C (or just close the window).
URL="http://127.0.0.1:8188"

if curl -s -o /dev/null -m 2 "$URL/system_stats"; then
  echo "ComfyUI is already running - opening it in your browser."
  open "$URL"
  exit 0
fi

cd "$(dirname "$0")/ComfyUI" || exit 1
export PYTORCH_ENABLE_MPS_FALLBACK=1
echo "Starting ComfyUI... your browser will open when it is ready."
echo "Leave this window open while you use ComfyUI. Press Ctrl+C here to stop it."
exec .venv/bin/python main.py --auto-launch
