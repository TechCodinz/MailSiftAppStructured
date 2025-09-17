FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Expose Flask default port
EXPOSE 5000

# Env defaults (override in runtime)
ENV PRICE_USDT=19 \
    FREE_SCRAPE_LIMIT=3

# Gunicorn entry (app module is server:app)
CMD ["gunicorn", "server:app", "--bind=0.0.0.0:5000", "--workers=2", "--threads=4", "--timeout=60"]

