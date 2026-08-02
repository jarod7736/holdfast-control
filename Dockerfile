# Holdfast control plane.
#
# `server/` is not part of the installed distribution (pyproject packages only
# src/), so it is copied next to the install and run with WORKDIR=/app, which
# puts it on sys.path for `python -m server`.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependency layer: changes only when packaging metadata changes.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir .

# Application layer.
COPY server/ ./server/

ENV HOLDFAST_DB_PATH=/data/control-plane.db \
    HOLDFAST_HOST=0.0.0.0 \
    HOLDFAST_PORT=8000

RUN useradd --system --uid 10001 --create-home holdfast \
    && mkdir -p /data \
    && chown -R holdfast:holdfast /data /app
USER holdfast

EXPOSE 8000

CMD ["python", "-m", "server"]
