#!/bin/bash
# Penguin's Storyboard setup. Double-click this file (or run ./setup.command) to install what the app needs:
# Homebrew, ffmpeg, LilyPond, the picture and video engine (ComfyUI) with its Python parts, Ollama and,
# if you want the app to write music, Claude Code.
#
# The big model files are NOT downloaded here. Start the app afterwards and use its Models page for those.
# It is safe to run again: anything already installed is left alone.
#
#   ./setup.command --check   only report what is installed, change nothing
#   ./setup.command --gguf    also install the add-on that lets ComfyUI read GGUF prompt readers
cd "$(dirname "$0")" || exit 1
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"

CHECK=0; GGUF=0
for a in "$@"; do case "$a" in --check) CHECK=1 ;; --gguf) GGUF=1 ;; esac; done

# The engine version the app was built and tested with. To try another: COMFY_REF=<commit or tag> ./setup.command
COMFY_REPO="https://github.com/comfyanonymous/ComfyUI.git"
COMFY_REF="${COMFY_REF:-c844cb672186f187bf9455e2808821417a10d296}"
GGUF_REPO="https://github.com/city96/ComfyUI-GGUF.git"
GGUF_REF="6ea2651e7df66d7585f6ffee804b20e92fb38b8a"

bold() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
miss() { printf '  \033[31m✗\033[0m %s\n' "$*"; }
have() { command -v "$1" >/dev/null 2>&1; }
problems=0

bold "Setting up Penguin's Storyboard"
[ "$CHECK" = 1 ] && echo "  (checking only: nothing will be installed)"

# --- This computer ---------------------------------------------------------------------------
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
  ok "Apple Silicon Mac"
else
  warn "This app was built and tested on Apple Silicon Macs only. On $(uname -sm) it may not work."
fi
if [ "$(uname -s)" = "Darwin" ]; then
  mem_gb=$(( $(sysctl -n hw.memsize) / 1073741824 ))
  if [ "$mem_gb" -ge 24 ]; then ok "$mem_gb GB of memory"; else warn "$mem_gb GB of memory. The video model wants 24 GB or more; expect it to be slow or to fail."; fi
fi
free_gb=$(df -g . | awk 'NR==2 {print $4}')
if [ "${free_gb:-0}" -ge 70 ]; then ok "$free_gb GB of free disk space"; else warn "${free_gb:-?} GB of free disk space. The models need about 55 GB and the engine about 5 GB."; fi

# --- Homebrew (macOS package manager) --------------------------------------------------------
if ! have brew; then
  if [ "$CHECK" = 1 ]; then miss "Homebrew is not installed"; problems=1
  else
    bold "Installing Homebrew. It may ask for your Mac password."
    /bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
    for b in /opt/homebrew/bin/brew /usr/local/bin/brew; do [ -x "$b" ] && eval "$("$b" shellenv)"; done
  fi
fi
have brew && ok "Homebrew"

# need <command> <brew formula> <what it is for>
need() {
  if have "$1"; then ok "$3"; return 0; fi
  if [ "$CHECK" = 1 ]; then miss "$3 is not installed (brew install $2)"; problems=1; return 1; fi
  bold "Installing $3"
  if have brew && brew install "$2" && have "$1"; then ok "$3"; else miss "$3 could not be installed. Try: brew install $2"; problems=1; return 1; fi
}
need git git "git"
need ffmpeg ffmpeg "ffmpeg (joins the clips and the music)"
need lilypond lilypond "LilyPond (sheet music and the notes the music is played from)"
need uv uv "uv (sets up the engine's Python)"
need ollama ollama "Ollama (runs the script-writing model on this computer)"

# --- The picture and video engine (ComfyUI) --------------------------------------------------
if [ -f ComfyUI/main.py ]; then
  ok "ComfyUI is in the ComfyUI folder"
elif [ "$CHECK" = 1 ]; then
  miss "ComfyUI is not installed yet"; problems=1
else
  bold "Downloading ComfyUI (the picture and video engine)"
  mkdir -p ComfyUI && (cd ComfyUI && git init -q && git remote add origin "$COMFY_REPO" \
    && git fetch -q --depth 1 origin "$COMFY_REF" && git checkout -q FETCH_HEAD)
  if [ -f ComfyUI/main.py ]; then ok "ComfyUI"; else miss "ComfyUI could not be downloaded. Check the internet connection and run setup again."; problems=1; fi
fi
PY=ComfyUI/.venv/bin/python
engine_ready() { [ -x "$PY" ] && "$PY" -c "import torch, aiohttp, av, huggingface_hub" >/dev/null 2>&1; }
if [ -f ComfyUI/main.py ]; then
  if engine_ready; then
    ok "The engine's Python parts ($("$PY" -c 'import torch; print("torch", torch.__version__)' 2>/dev/null))"
  elif [ "$CHECK" = 1 ]; then
    miss "The engine's Python parts are not installed yet"; problems=1
  else
    bold "Installing the engine's Python parts (a few gigabytes, this takes a while)"
    [ -x "$PY" ] || uv venv --python 3.13 ComfyUI/.venv
    uv pip install --python "$PY" -r ComfyUI/requirements.txt "huggingface_hub>=1.4"
    if engine_ready; then ok "The engine's Python parts"; else miss "The engine's Python parts did not install. Run setup again to retry."; problems=1; fi
  fi
fi

# --- Optional: GGUF prompt readers -----------------------------------------------------------
if [ -d ComfyUI/custom_nodes/ComfyUI-GGUF ]; then
  ok "GGUF add-on for ComfyUI (ComfyUI-GGUF)"
elif [ "$GGUF" = 1 ] && [ "$CHECK" = 0 ] && [ -f ComfyUI/main.py ]; then
  bold "Installing the GGUF add-on for ComfyUI"
  mkdir -p ComfyUI/custom_nodes/ComfyUI-GGUF && (cd ComfyUI/custom_nodes/ComfyUI-GGUF && git init -q \
    && git remote add origin "$GGUF_REPO" && git fetch -q --depth 1 origin "$GGUF_REF" && git checkout -q FETCH_HEAD)
  uv pip install --python "$PY" "gguf>=0.13.0" sentencepiece protobuf
  [ -f ComfyUI/custom_nodes/ComfyUI-GGUF/nodes.py ] && ok "GGUF add-on" || { miss "The GGUF add-on could not be installed"; problems=1; }
else
  echo "  - GGUF add-on: not installed. Only needed if you choose a GGUF prompt reader on the Models page (./setup.command --gguf)."
fi

# --- Music: Claude Code composes, a Chrome-family browser turns the score into sound ---------
if have claude; then
  ok "Claude Code $(claude --version 2>/dev/null | awk '{print $1}')"
  if claude auth status 2>/dev/null | grep -q '"loggedIn": *true'; then ok "Claude Code is signed in"
  elif [ "$CHECK" = 1 ]; then warn "Claude Code is not signed in. Run claude once in Terminal and sign in."
  else
    bold "Claude Code needs you to sign in once (only for writing music)."
    echo "  Claude will open now. Sign in, then type /exit to come back here."
    read -r -p "  Press Return to sign in (or type s to skip): " answer
    [ "$answer" != "s" ] && claude
  fi
elif [ "$CHECK" = 1 ]; then
  warn "Claude Code is not installed. Everything works without it except writing music."
else
  bold "Claude Code writes the music for your movies. Everything else works without it."
  read -r -p "  Install Claude Code now? (Return = yes, s = skip): " answer
  if [ "$answer" != "s" ]; then
    curl -fsSL https://claude.ai/install.sh | bash
    export PATH="$HOME/.local/bin:$PATH"
    if have claude; then ok "Claude Code installed. Run setup again, or run claude once, to sign in."; else warn "Claude Code could not be installed: https://claude.com/claude-code"; fi
  fi
fi
browser=""
for b in "Google Chrome" "Chromium" "Microsoft Edge" "Brave Browser"; do
  [ -d "/Applications/$b.app" ] || [ -d "$HOME/Applications/$b.app" ] && { browser="$b"; break; }
done
if [ -n "$browser" ]; then ok "$browser (turns written music into an audio file)"
else warn "No Chrome, Chromium, Edge or Brave found. Music cannot be turned into an audio file without one of them."; fi

chmod +x "Start Storyboard.command" setup.command 2>/dev/null
if [ "$problems" = 0 ]; then
  bold "All set. Double-click \"Start Storyboard.command\", then open Models in the app to download the model files."
else
  bold "Not everything is in place yet (see the ✗ lines above)."
  [ "$CHECK" = 1 ] && echo "  Run ./setup.command without --check to install what is missing."
fi
[ -t 0 ] && [ -z "${STORYBOARD_FROM_START:-}" ] && [ "$CHECK" = 0 ] && read -r -p "Press Return to close." _
exit $problems
