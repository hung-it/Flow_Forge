FROM python:3.12.12-slim
WORKDIR /app
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
COPY pyproject.toml uv.lock ./
COPY src ./src
COPY migrations ./migrations
COPY alembic.ini ./
COPY tests ./tests
COPY integration ./integration
RUN uv sync --frozen
ENV PATH="/app/.venv/bin:$PATH"
RUN useradd --uid 10001 --create-home app
USER app
CMD ["uvicorn", "flowforge.api:app", "--host", "0.0.0.0", "--port", "8000"]
