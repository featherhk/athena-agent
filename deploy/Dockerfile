FROM ghcr.io/astral-sh/uv:0.9.30-bookworm-slim AS runtime
ENV UV_PYTHON_INSTALL_DIR=/opt/python UV_PROJECT_ENVIRONMENT=/opt/venv
WORKDIR /opt/athena
COPY pyproject.toml uv.lock .python-version ./
RUN uv python install 3.14t && uv sync --frozen --no-dev --no-install-project --python 3.14t
COPY backend ./backend
COPY dist ./dist
COPY deploy/entrypoint.py ./deploy/entrypoint.py
ENV PYTHONPATH=/opt/athena/backend PYTHONDONTWRITEBYTECODE=1
RUN /opt/venv/bin/python -m athena.doctor && useradd --uid 10001 --create-home athena
USER 10001:10001
ENTRYPOINT ["/opt/venv/bin/python", "/opt/athena/deploy/entrypoint.py"]
