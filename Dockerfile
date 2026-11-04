FROM ghcr.io/astral-sh/uv:0.13.5 AS uv

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PATH="/app/.venv/bin:${PATH}"

WORKDIR /app

COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE NOTICE ./
COPY src ./src

RUN uv sync --frozen --no-dev \
    && useradd --create-home --uid 10001 app \
    && mkdir -p /home/app/.cache/jvdata-bridge-mcp/models \
    && chown -R app:app /home/app/.cache \
    && chown -R app:app /app

USER app

EXPOSE 8080

ENTRYPOINT ["jvdata-bridge-mcp"]
CMD ["serve", "--transport", "streamable-http", "--host", "0.0.0.0", "--port", "8080"]
