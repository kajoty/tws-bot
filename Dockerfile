# Dockerfile für IB Trading Bot
FROM python:3.11-slim

# Metadata
LABEL maintainer="tws-bot"
LABEL description="Interactive Brokers Trading Bot mit IB Gateway Support"

# Arbeitsverzeichnis erstellen
WORKDIR /app

# System-Dependencies installieren
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Python-Dependencies installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Bot-Code kopieren
COPY *.py ./
COPY .github/ ./.github/

# Verzeichnisse für persistente Daten erstellen
RUN mkdir -p /app/data /app/logs /app/plots

# Volumes für persistente Daten
VOLUME ["/app/data", "/app/logs", "/app/plots"]

# Umgebungsvariablen (können in docker-compose überschrieben werden)
ENV IB_HOST=ib-gateway
ENV IB_PORT=4002
ENV IS_PAPER_TRADING=True
ENV DRY_RUN=False
ENV PYTHONUNBUFFERED=1

# Health Check
HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -c "import os; exit(0 if os.path.exists('/app/data/trading_data.db') else 1)"

# Bot starten
CMD ["python", "-u", "main.py"]
