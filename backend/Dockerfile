# Backend image: FastAPI API server + pipeline consumer (same image).
# Entrypoint is the API server; the pipeline service overrides it in compose.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

# System dependency for pdf2image (PDF -> images).
RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Faster, reproducible installs; copy (not hardlink) into the image layer.
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# 1) Install dependencies first (cached layer). The project is an application
#    (tool.uv.package = false), so this installs deps only — no torch, since
#    sentence-transformers is an optional extra we don't enable here.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev

# 2) Copy the application code.
COPY . .

EXPOSE 8000

# Default process = the API server (the pipeline service overrides `entrypoint`).
ENTRYPOINT ["uv", "run", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
