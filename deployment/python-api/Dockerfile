# syntax=docker/dockerfile:1
# Context: repository root; the adjacent Dockerfile.dockerignore is mandatory.
FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HOME=/tmp/stock-comp \
    XDG_CACHE_HOME=/tmp/stock-comp/cache \
    STOCK_COMP_CACHE_DIR=/tmp/stock-comp/market-data

WORKDIR /app
COPY requirements-web-api.txt ./
COPY deployment/python-api/requirements.txt ./deployment/python-api/requirements.txt
RUN python -m pip install -r deployment/python-api/requirements.txt \
    && python -m pip check \
    && mkdir -p /tmp/stock-comp/market-data \
    && chown -R 10001:10001 /tmp/stock-comp

# Copy only the domain packages needed by the existing public API.
# The ignore allowlist limits each directory to reviewed source files.
COPY config.py ./
COPY api/ ./api/
COPY data/ ./data/
COPY market/ ./market/
COPY paper_trading/ ./paper_trading/
COPY strategies/ ./strategies/
COPY deployment/python-api/serve.py ./deployment/python-api/serve.py

USER 10001:10001
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "deployment/python-api/serve.py", "--check-health"]
CMD ["python", "deployment/python-api/serve.py"]
