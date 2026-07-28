FROM python:3.11-slim

WORKDIR /app

# Install deps first for better layer caching.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

# Runtime dirs (config + rules are typically bind-mounted).
COPY nxre.config.example.yaml ./
RUN mkdir -p rules automations

EXPOSE 8787

# Password(s) come from the environment: NXRE__<SYSTEM>__PASSWORD
# Config is expected at /app/nxre.config.yaml (bind-mount it).
CMD ["nxre", "serve"]
