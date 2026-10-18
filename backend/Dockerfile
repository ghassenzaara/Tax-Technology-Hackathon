# Production image of the HTTP API. Built by compose.prod.yml in the repository root.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.8.0 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so code changes do not reinstall them.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-dev --no-install-project

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000
# The entry point always uses a selector event loop (psycopg async needs it).
CMD ["python", "-m", "app.api", "--host", "0.0.0.0", "--port", "8000"]
