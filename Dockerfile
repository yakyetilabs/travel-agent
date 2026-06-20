# Orchestrator container. We use a Dockerfile (not Cloud Run buildpacks) for two
# reasons: (1) the buildpack runtime registry lacks some exact Python patch
# versions (e.g. 3.12.11), and (2) this project uses `uv`, which the Python
# buildpack doesn't natively install. Docker Hub has the exact version, and uv
# gives fast, lockfile-pinned installs.

FROM python:3.12.11-slim

# uv binary (pin a version for reproducibility; bump deliberately).
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app

# Install dependencies first as a cached layer (only re-runs when deps change).
# package = false in pyproject means uv installs deps into .venv without building
# the project itself.
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# Application source.
COPY . .

# Run from the project venv.
ENV PATH="/app/.venv/bin:$PATH"

# Cloud Run injects $PORT; default to 8080 locally.
ENV PORT=8080
EXPOSE 8080
CMD ["sh", "-c", "uvicorn server:app --host 0.0.0.0 --port ${PORT}"]
