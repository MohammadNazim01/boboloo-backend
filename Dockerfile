# ==============================
# BASE IMAGE
# ==============================
FROM python:3.11-slim

# ==============================
# WORKDIR
# ==============================
WORKDIR /app

# ==============================
# PYTHON SETTINGS
# ==============================
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# ==============================
# SYSTEM DEPENDENCIES
# ==============================
RUN apt-get update && apt-get install -y \
    gcc \
    libpq-dev \
    curl \
    && rm -rf /var/lib/apt/lists/*

# ==============================
# INSTALL PYTHON DEPENDENCIES
# ==============================
COPY requirements.txt .

RUN pip install --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt && \
    python -m nltk.downloader vader_lexicon

# ==============================
# COPY PROJECT
# ==============================
COPY . .

# ==============================
# CREATE NON ROOT USER
# ==============================
RUN adduser --disabled-password --gecos "" appuser

RUN chown -R appuser:appuser /app

USER appuser

# ==============================
# PORT
# ==============================
EXPOSE 8080

# ==============================
# HEALTHCHECK
# ==============================
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
CMD curl --fail http://localhost:8080/health || exit 1

# ==============================
# START SERVER
# ==============================
CMD ["gunicorn","-k","uvicorn.workers.UvicornWorker","app.main:app","--bind","0.0.0.0:8080","--workers","1","--timeout","60"]