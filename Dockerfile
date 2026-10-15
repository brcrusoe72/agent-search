ARG PYTHON_BASE_IMAGE=python@sha256:9e01bf1ae5db7649a236da7be1e94ffbbbdd7a93f867dd0d8d5720d9e1f89fab
FROM ${PYTHON_BASE_IMAGE}

# System dependency for YouTube transcript extraction.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Copy adapters and create data directory
COPY adapters/ ./adapters/
RUN groupadd --system --gid 10001 agentsearch \
    && useradd --system --uid 10001 --gid agentsearch --home-dir /app agentsearch \
    && mkdir -p /app/data \
    && chown -R agentsearch:agentsearch /app

USER agentsearch

EXPOSE 3939

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3939"]
