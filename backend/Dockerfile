FROM python:3.12.12-slim
WORKDIR /workspace/backend
COPY --from=ghcr.io/astral-sh/uv:0.11.0 /uv /usr/local/bin/uv
COPY backend/pyproject.toml backend/uv.lock ./
COPY backend/src ./src
COPY backend/migrations ./migrations
COPY backend/alembic.ini ./
COPY backend/tests ./tests
COPY backend/integration ./integration
COPY examples /workspace/examples
RUN uv sync --frozen
ENV PATH="/workspace/backend/.venv/bin:$PATH"
ENV PYTEST_ADDOPTS="-o cache_dir=/tmp/flowforge-pytest"
ENV COVERAGE_FILE="/tmp/flowforge.coverage"
RUN useradd --uid 10001 --create-home app
USER app
CMD ["uvicorn", "flowforge.api:app", "--host", "0.0.0.0", "--port", "8000", "--no-proxy-headers", "--no-access-log"]
