#!/usr/bin/env bash
# ====================================================================
# Render Native Build Script (for Python runtime)
# ====================================================================
set -e

echo "[Render Build] Upgrading pip and installing Python dependencies..."
python -m pip install --upgrade pip setuptools wheel
python -m pip install -r requirements.txt
python -m pip install -e .

# Check if ffmpeg is available
if ! command -v ffmpeg &> /dev/null; then
    echo "[Render Build] FFmpeg not found in PATH. Installing static build..."
    mkdir -p bin
    curl -sL https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz | tar -xJ --strip-components=1 -C bin/
    export PATH="$PWD/bin:$PATH"
    echo "[Render Build] FFmpeg installed successfully at $PWD/bin/ffmpeg"
else
    echo "[Render Build] System FFmpeg found: $(which ffmpeg)"
fi

echo "[Render Build] Preparing runtime directories..."
mkdir -p work queue assets/gameplay/gta5 assets/gameplay/minecraft assets/gameplay/subway_surfers

echo "[Render Build] Build completed successfully!"
