# vox-conjurata Audio Generation (TTS) Dockerfile
FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install dependencies
RUN apt-get update && apt-get install -y \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY tts_engine.py .

EXPOSE 8000

CMD ["python3", "tts_engine.py"]
