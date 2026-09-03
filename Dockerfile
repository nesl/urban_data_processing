FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app
COPY pyproject.toml README.md /app/
COPY observation_pipeline /app/observation_pipeline
COPY processing /app/processing
COPY replay /app/replay
COPY urban_observation_model /app/urban_observation_model
RUN pip install --no-cache-dir --no-deps /app

FROM base AS replay
CMD ["python", "-m", "replay.replay", "--help"]

FROM base AS processing
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt
CMD ["python", "-m", "processing.enrichment.service", "--help"]
