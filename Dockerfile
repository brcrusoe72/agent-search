FROM python:3.12-slim

# System deps for PDF extraction and yt-dlp
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/

# Create data and adapters directories
RUN mkdir -p /app/data /app/adapters

EXPOSE 3939

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "3939"]
