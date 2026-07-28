FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Runtime dirs (config + rules are typically bind-mounted). Ship the example as the
# default config so `serve` works out of the box (assumes NX at 127.0.0.1:7001).
COPY nxre.config.example.yaml ./
RUN cp nxre.config.example.yaml nxre.config.yaml && mkdir -p rules automations

EXPOSE 8787

# Browser-login token is cached here (persist it with a volume if you want it to
# survive restarts). Open http://127.0.0.1:8787 and sign in with your NX account.
ENV NXRE_SESSION_FILE=/app/session.json

# Optional, only for unattended startup with no browser: NXRE__<SYSTEM>__PASSWORD.
# Config is expected at /app/nxre.config.yaml (bind-mount to override the default).
CMD ["nxre", "serve"]
