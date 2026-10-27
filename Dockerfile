# Atlas web image (Render, or any Docker host).
#   docker build -t atlas .
#   docker run -p 8000:8000 --env-file .env atlas
FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Dependencies first, so a code change reuses this layer.
COPY pyproject.toml uv.lock .python-version README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# Editable install: the questionnaire is read from /app/config next to src/.
COPY config ./config
COPY src ./src
COPY wsgi.py ./
RUN uv sync --frozen --no-dev

RUN useradd --create-home atlas && mkdir instance && chown atlas instance
USER atlas
ENV MPLCONFIGDIR=/tmp/matplotlib

EXPOSE 8000
# Migrations run when the app starts; PORT is set by Render.
CMD ["sh", "-c", "exec waitress-serve --host 0.0.0.0 --port ${PORT:-8000} --threads 4 wsgi:app"]
