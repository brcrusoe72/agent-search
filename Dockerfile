ARG PYTHON_BASE_IMAGE=python@sha256:9e01bf1ae5db7649a236da7be1e94ffbbbdd7a93f867dd0d8d5720d9e1f89fab
FROM ${PYTHON_BASE_IMAGE}

# System deps for PDF extraction, yt-dlp, and browser-render extraction
RUN apt-get update && apt-get install -y --no-install-recommends \
    chromium \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Copy adapters and create data directory
COPY adapters/ ./adapters/
RUN mkdir -p /app/data

EXPOSE 3939

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3939"]
