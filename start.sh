#!/usr/bin/env bash
set -euo pipefail

# Link /runpod-volume -> /workspace so volume venvs and relative paths match Pod layout
if [ -d "/runpod-volume" ] && [ ! -e "/workspace" ]; then
    ln -s /runpod-volume /workspace
fi

COMFY_DIR="${COMFY_DIR:-/workspace/runpod-slim/ComfyUI}"
if [ ! -d "$COMFY_DIR" ]; then
    echo "ERROR: ComfyUI directory not found at $COMFY_DIR" >&2
    exit 1
fi

# Auto-detect the volume's Python environment
COMFY_PYTHON=""
for candidate in \
    "/workspace/runpod-slim/ComfyUI/.venv/bin/python" \
    "/workspace/runpod-slim/ComfyUI/venv/bin/python" \
    "/workspace/runpod-slim/.venv/bin/python" \
    "/workspace/runpod-slim/venv/bin/python"; do
    if [ -x "$candidate" ]; then
        COMFY_PYTHON="$candidate"
        break
    fi
done

if [ -z "$COMFY_PYTHON" ]; then
    COMFY_PYTHON="$(find /workspace/runpod-slim -maxdepth 4 -type f -path '*/bin/python*' 2>/dev/null | head -n 1 || true)"
fi

if [ -z "$COMFY_PYTHON" ]; then
    echo "WARN: No venv python found on volume, falling back to system python3" >&2
    COMFY_PYTHON="python3"
fi

echo "Using ComfyUI Python: $COMFY_PYTHON"

mkdir -p /tmp/comfy_input /tmp/comfy_output /tmp/comfy_temp

cd "$COMFY_DIR"
"$COMFY_PYTHON" -u main.py \
    --listen 127.0.0.1 \
    --port 8188 \
    --disable-auto-launch \
    --input-directory /tmp/comfy_input \
    --output-directory /tmp/comfy_output \
    --temp-directory /tmp/comfy_temp \
    >/tmp/comfyui.log 2>&1 &

COMFY_PID=$!
echo "Started ComfyUI (PID $COMFY_PID), waiting for readiness..."

for i in $(seq 1 120); do
    if ! kill -0 "$COMFY_PID" 2>/dev/null; then
        echo "ERROR: ComfyUI exited early. Last 60 lines of /tmp/comfyui.log:" >&2
        tail -n 60 /tmp/comfyui.log >&2 || true
        exit 1
    fi
    if curl -sf http://127.0.0.1:8188/system_stats >/dev/null 2>&1; then
        echo "ComfyUI is ready."
        break
    fi
    sleep 1
done

exec python3 -u /app/handler.py
