FROM python:3.11-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app
WORKDIR /app

FROM base AS replay
COPY pyproject.toml README.md /app/
COPY observation_pipeline /app/observation_pipeline
COPY processing /app/processing
COPY replay /app/replay
COPY urban_observation_model /app/urban_observation_model
RUN pip install --no-cache-dir --no-deps /app
CMD ["python", "-m", "replay.replay", "--help"]

FROM base AS processing
ARG PYTORCH_VERSION=2.7.1
ARG PYTORCH_INDEX_URL=https://download.pytorch.org/whl/cu118
COPY requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir "torch==${PYTORCH_VERSION}" --index-url "${PYTORCH_INDEX_URL}" \
    && pip install --no-cache-dir -r /tmp/requirements.txt
COPY pyproject.toml README.md /app/
COPY observation_pipeline /app/observation_pipeline
COPY processing /app/processing
COPY replay /app/replay
COPY urban_observation_model /app/urban_observation_model
RUN pip install --no-cache-dir --no-deps /app
CMD ["python", "-m", "processing.enrichment.service", "--help"]
