# Railway-ready Dockerfile for MockAPI Pro.
#
# The Flask app lives in backend/ and serves the static frontend from
# ../frontend (relative to the working directory), so both directories must
# be present in the image and the WORKDIR must be /app/backend.

FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# Install dependencies first so the layer is cached across builds.
COPY backend/requirements.txt ./backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

# Copy the application source. frontend/ is served by Flask as static files,
# and uploads/storage are created at runtime on the container's writable layer.
COPY backend/ ./backend/
COPY frontend/ ./frontend/

# send_from_directory('../frontend', ...) and static_folder='../frontend'
# resolve against the working directory, so it must be /app/backend.
WORKDIR /app/backend

EXPOSE 5000

# gunicorn in production, honoring Railway's $PORT (default 5000).
# Single worker + threads to avoid SQLite lock contention between processes.
CMD gunicorn --bind 0.0.0.0:${PORT:-5000} --workers 1 --threads 8 --timeout 120 app:app
