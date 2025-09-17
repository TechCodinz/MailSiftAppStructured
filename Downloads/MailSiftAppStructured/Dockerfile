# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# System deps for pdf/image extras
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    tesseract-ocr \
    poppler-utils \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.txt
RUN pip install -r requirements.txt

COPY . .

# Default envs
ENV FREE_SCRAPE_QUOTA=3 \
    SESSION_COOKIE_SECURE=true \
    ENVIRONMENT=production

EXPOSE 8000

CMD ["gunicorn", "server:app", "--bind=0.0.0.0:8000", "--workers=2", "--threads=4", "--timeout=60"]

