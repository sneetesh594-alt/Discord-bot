FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY requirements.txt ./
RUN python -m pip install -r requirements.txt
COPY pyproject.toml ./
COPY aegis ./aegis
RUN python -m pip install --no-deps . \
    && useradd --create-home --uid 10001 aegis \
    && mkdir -p /app/data /app/logs \
    && chown -R aegis:aegis /app
COPY config.yaml ./config.yaml
COPY scripts/backup.py ./scripts/backup.py
USER aegis
CMD ["python", "-m", "aegis"]
