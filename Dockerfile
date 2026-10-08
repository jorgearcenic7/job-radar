FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock .
RUN python -m pip install \
    --no-cache-dir \
    --require-hashes \
    -r requirements.lock

RUN useradd --create-home --uid 10001 appuser

COPY --chown=appuser:appuser main.py .
COPY --chown=appuser:appuser job_radar ./job_radar
COPY --chown=appuser:appuser scripts/migrate.py ./scripts/migrate.py
COPY --chown=appuser:appuser scripts/source_health.py ./scripts/source_health.py
COPY --chown=appuser:appuser sql ./sql

USER appuser

CMD ["python", "main.py"]
