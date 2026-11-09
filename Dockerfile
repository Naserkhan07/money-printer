# ====================================================================
# AUTO-POSTER — Autonomous Shorts & Reels Engine
# Production Container Image for Render & Cloud Environments
# ====================================================================
FROM python:3.12-slim-bookworm

# Prevent python from buffering stdout/stderr and generating .pyc files
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DEBIAN_FRONTEND=noninteractive \
    PORT=10000

# Install system dependencies:
# - FFmpeg & FFprobe for audio extraction, video cropping, and gameplay compositing
# - fonts-liberation, fonts-dejavu-core & fontconfig for ASS karaoke subtitles
# - Node.js for yt-dlp JavaScript challenge solver
# - curl & git for runtime tooling
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    fonts-liberation \
    fonts-dejavu-core \
    fontconfig \
    nodejs \
    curl \
    git \
    build-essential \
    && fc-cache -f \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy dependency files first to maximize Docker layer caching
COPY requirements.txt pyproject.toml /app/

# Install Python dependencies
RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir -e .

# Copy full application source code
COPY . /app/

# Create runtime working and spool directories
RUN mkdir -p /app/work /app/queue /app/assets/gameplay/gta5 /app/assets/gameplay/minecraft /app/assets/gameplay/subway_surfers

# Expose Render default port
EXPOSE 10000

# Unified cloud entrypoint (Runs Mission Control Dashboard on $PORT + 24/7 Automation Worker)
CMD ["python", "run_render.py"]

