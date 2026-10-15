# The DSN-MUI connector as a container, for the platform's Airflow (Metamorph-Knowledge-Platform/dags).
# Build from the workspace root, the directory holding every repository, so the contract package sits where
# pyproject.toml's path source expects it (../Metamorph-Knowledge-Platform/packages/metamorph-contract):
#   docker build -f DSN-MUI-Source-Connector/Dockerfile -t metamorph/dsn-mui-connector:dev .
# data/ is not in the image: Airflow bind-mounts the repository's data/ directory at runtime.
FROM python:3.11-slim
COPY --from=ghcr.io/astral-sh/uv:0.9.27 /uv /usr/local/bin/uv
RUN apt-get update \
    && apt-get install --yes --no-install-recommends tesseract-ocr tesseract-ocr-ara \
    && rm -rf /var/lib/apt/lists/*

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH=/opt/venv/bin:$PATH
# Bundles land in a named volume that takes this directory's owner the first time it is mounted.
RUN useradd --create-home --uid 1000 connector && mkdir /bundles && chown connector /bundles

WORKDIR /workspace/DSN-MUI-Source-Connector
COPY Metamorph-Knowledge-Platform/packages/metamorph-contract /workspace/Metamorph-Knowledge-Platform/packages/metamorph-contract
COPY DSN-MUI-Source-Connector/pyproject.toml ./pyproject.toml
COPY DSN-MUI-Source-Connector/uv.lock ./uv.lock
COPY DSN-MUI-Source-Connector/README.md ./README.md
COPY DSN-MUI-Source-Connector/dsn_ingest ./dsn_ingest
COPY DSN-MUI-Source-Connector/scripts ./scripts
COPY DSN-MUI-Source-Connector/source_connector.py ./source_connector.py
RUN uv sync --frozen --no-dev

USER connector
CMD ["dsn", "help"]
