# ════════════════════════════════════════════════════════════════════════════
# Dockerfile — Multi-stage build for k8s-copilot
# ════════════════════════════════════════════════════════════════════════════
#
# CONCEPT: MULTI-STAGE BUILDS
# ═══════════════════════════
# A multi-stage build uses multiple FROM statements in one Dockerfile.
# Each FROM starts a new "stage" with a clean filesystem.
# You can COPY files from earlier stages into later ones.
#
# WHY MULTI-STAGE?
#   Stage 1 (builder): Install build tools and compile dependencies.
#     → This stage can be large (build tools, compilers, dev headers).
#   Stage 2 (runtime): Copy only the compiled output from stage 1.
#     → This stage is small — no build tools, just what's needed to run.
#
# Result: the final image is only the runtime stage.
# Build tools never end up in the deployed image.
# Typical reduction: 400MB → 120MB for a Python project.
#
# INTERVIEW TALKING POINT:
#   "I used a multi-stage build. The builder stage installs all dependencies
#   including gcc and build headers needed for the kubernetes SDK.
#   The runtime stage is a slim Python image with only the installed packages.
#   This cut image size significantly and reduces attack surface."
# ════════════════════════════════════════════════════════════════════════════


# ── Stage 1: Builder ─────────────────────────────────────────────────────────
# python:3.11-slim: official Python image, "slim" variant strips unused packages.
# We use this for the builder too (not the full image) because we install
# via pip wheels which don't need compilers in most cases.
FROM python:3.11-slim AS builder
# AS builder: names this stage "builder" so we can reference it later

# Set the working directory inside the container.
# All subsequent COPY and RUN commands operate relative to this.
WORKDIR /build

# COPY requirements BEFORE source code.
# WHY? Docker layer caching:
#   Each instruction creates a layer. Layers are cached and reused
#   if the inputs haven't changed. If we copied all source first,
#   ANY code change would invalidate the pip install layer.
#   By copying requirements.txt first, pip install is only re-run
#   when requirements.txt changes — not every time you edit a .py file.
#   This makes rebuilds dramatically faster during development.
COPY requirements.txt .

# Install dependencies into a specific directory (/install) rather than
# the system Python. This makes it easy to copy just the packages
# to the runtime stage without bringing along build tools.
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt
# --no-cache-dir: don't cache downloaded wheels (saves space in this layer)
# --prefix=/install: install to /install instead of system Python paths


# ── Stage 2: Runtime ─────────────────────────────────────────────────────────
# Fresh, clean base image. No build artifacts from stage 1.
FROM python:3.11-slim AS runtime

# Security best practice: run as a non-root user.
# By default, Docker containers run as root — risky if the container escapes.
# Creating a dedicated app user limits blast radius.
RUN useradd --create-home --shell /bin/bash appuser
# useradd: creates a system user
# --create-home: creates /home/appuser
# --shell /bin/bash: gives the user a shell (needed for some tools)

WORKDIR /app

# Copy installed packages from the builder stage.
# We copy to the system Python path so imports work without any PATH hacks.
COPY --from=builder /install /usr/local
# --from=builder: copy FROM the stage named "builder"
# /install → /usr/local: merges the installed packages into the system Python path

# Copy application source code.
# We copy the whole src/ directory as-is.
COPY src/ ./src/

# Switch to the non-root user for all subsequent commands (including CMD).
# Everything the app does at runtime runs as appuser, not root.
USER appuser

# Create the data directory for the SQLite checkpoint database.
# This is where checkpoints.db will be created.
# In Kubernetes, you'd mount a PersistentVolumeClaim here so the file
# survives pod restarts.
RUN mkdir -p /app/data

WORKDIR /app/src

# Environment variables with defaults.
# These can be overridden at runtime via -e flags or docker-compose env_file.
ENV DATA_PROVIDER=fixture
ENV CHECKPOINT_DB_PATH=/app/data/checkpoints.db
ENV LLM_MODEL=gemini-2.5-flash
ENV PYTHONUNBUFFERED=1
# PYTHONUNBUFFERED=1: don't buffer stdout/stderr.
# In containers, buffering can cause log lines to appear late or not at all.
# This makes logs appear immediately, which is critical for debugging.

# EXPOSE documents which port the container listens on.
# It's informational — it doesn't actually publish the port.
# You still need -p 8000:8000 in docker run or ports: in docker-compose.
EXPOSE 8000

# Health check: Docker will periodically call this to determine if the
# container is healthy. If it fails repeatedly, Docker marks it unhealthy.
# K8s uses liveness/readiness probes separately, but this is good practice
# for docker-compose environments.
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')"

# The default command: start the FastAPI server.
# Using uvicorn (ASGI server) directly — not via `python -m` to avoid
# Python's startup overhead.
# --host 0.0.0.0: listen on all interfaces (not just localhost).
#   CRITICAL: 127.0.0.1 (localhost) inside a container is only reachable
#   from inside the container. 0.0.0.0 makes it reachable from outside.
# --workers 1: one process (SQLite doesn't handle concurrent writes well).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
