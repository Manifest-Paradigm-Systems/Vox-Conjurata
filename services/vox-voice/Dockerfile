# vox-conjurata Voice Service Dockerfile
FROM docker.io/rocm/dev-ubuntu-22.04:latest

ENV HSA_OVERRIDE_GFX_VERSION=10.3.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install dependencies
COPY requirements.txt .
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy application code
COPY app.py .

EXPOSE 5000

CMD ["python3", "app.py"]
