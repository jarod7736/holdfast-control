# Holdfast control plane image.
# Runs `python -m server` from /app so the server/ package resolves from the
# working directory (it is not a setuptools-packaged module) and the repo-root
# web/ directory resolves for the /ui dashboard route.
FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml README.md ./
COPY src ./src
COPY server ./server
COPY web ./web

# Installs the holdfastctl package plus all runtime dependencies.
RUN pip install --no-cache-dir .

# HOME=/data puts the default SQLite path (~/.holdfast/control-plane.db) on the
# mounted volume. Override with HOLDFAST_DB_PATH if that changes.
ENV HOLDFAST_HOST=0.0.0.0 \
    HOLDFAST_PORT=8000 \
    HOME=/data

EXPOSE 8000
VOLUME /data

CMD ["python", "-m", "server"]
