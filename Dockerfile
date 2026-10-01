FROM python:3.12.14-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Apply Debian security patches for PCRE2 and OpenSSL without changing Python.
RUN apt-get update \
    && apt-get install --yes --no-install-recommends --only-upgrade \
        libpcre2-8-0 libssl3t64 openssl openssl-provider-legacy \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --system orderflow \
    && useradd --system --gid orderflow --home-dir /app orderflow

COPY pyproject.toml README.md LICENSE ./
COPY app ./app
RUN python -m pip install --no-cache-dir --upgrade "pip>=26.2,<27" \
    && python -m pip install --no-cache-dir . \
    && python -m pip uninstall --yes pip

COPY alembic.ini ./
COPY alembic ./alembic

RUN chown -R orderflow:orderflow /app
USER orderflow

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)"
CMD ["python", "-m", "app.server"]
