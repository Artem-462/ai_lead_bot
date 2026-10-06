# ── Stage 1: Base image ───────────────────────────────────────────────────────
FROM python:3.11-slim AS base

# Prevent Python from writing .pyc files and enable unbuffered output
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# ── Stage 2: Dependencies ─────────────────────────────────────────────────────
# Install deps in a separate layer so Docker can cache them efficiently.
# This layer is only rebuilt when requirements.txt changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ── Stage 3: Application code ─────────────────────────────────────────────────
COPY . .

# Create the data directory for SQLite persistence.
# In docker-compose this is overridden by a named volume.
RUN mkdir -p /app/data

# Run as non-root user for security
RUN adduser --disabled-password --gecos "" botuser \
    && chown -R botuser:botuser /app
USER botuser

# ── Runtime ───────────────────────────────────────────────────────────────────
CMD ["python", "main.py"]
