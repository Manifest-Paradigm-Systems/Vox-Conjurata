# Vox Vision - ROCm PyTorch Environment (SDXL Turbo)
FROM rocm/pytorch:latest

# Set ROCm environment variables
ENV HSA_OVERRIDE_GFX_VERSION=10.3.0
ENV DEBIAN_FRONTEND=noninteractive

WORKDIR /app

# Install system dependencies if needed
# RUN apt-get update && apt-get install -y ...

# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 7860

CMD ["python", "main.py"]
