FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Keep dependency installation cached when application code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /app/data

RUN adduser --disabled-password --gecos "" botuser \
    && chown -R botuser:botuser /app
USER botuser

# This checks imports and storage access, not polling or external API health.
HEALTHCHECK --interval=60s --timeout=15s --start-period=20s --retries=3 \
    CMD python -c "import os, tempfile, config, main, services.db, services.rag; assert os.path.isdir('data'); tempfile.TemporaryFile(dir='data').close()"

CMD ["python", "main.py"]
