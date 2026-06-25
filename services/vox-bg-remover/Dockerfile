# vox-bg-remover — Lightweight background removal service
FROM python:3.11-slim

WORKDIR /app

RUN pip install --no-cache-dir "rembg[cpu]" fastapi uvicorn python-multipart

COPY app.py .

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
