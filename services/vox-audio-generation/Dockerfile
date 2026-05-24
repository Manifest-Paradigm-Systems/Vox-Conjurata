# Vox Audio Generation - ROCm PyTorch Environment
FROM rocm/pytorch:latest

# Set ROCm environment variables
ENV HSA_OVERRIDE_GFX_VERSION=10.3.0

WORKDIR /app

# COPY requirements.txt .
# RUN pip install --no-cache-dir -r requirements.txt

EXPOSE 8000

CMD ["python", "app.py"]
