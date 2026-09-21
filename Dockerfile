FROM python:3.12.14-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN groupadd --system orderflow \
    && useradd --system --gid orderflow --home-dir /app orderflow

COPY pyproject.toml README.md ./
COPY app ./app
RUN python -m pip install --no-cache-dir .

COPY alembic.ini ./
COPY alembic ./alembic

RUN chown -R orderflow:orderflow /app
USER orderflow

EXPOSE 8000
CMD ["python", "-m", "app.server"]
