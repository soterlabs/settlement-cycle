# Image for the Railway services (settle-api, settle-cron). One image, two
# start commands (see .railway/railway.ts). Explicit on purpose: the Nixpacks
# Python builder installed the `api` extra outside its runtime venv and the
# container started with `uvicorn: command not found`.
# 3.11 to match requires-python / CI / the ruff+mypy target.
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cache-friendly): metadata + the package itself.
# README.md is COPYied because pyproject declares it as the package readme;
# hatchling fails metadata generation without it.
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install -e ".[api]"

# Everything the pipeline reads at runtime: config/, scripts/, db/schema.sql,
# settlements/ (SNR cross-checks). .dockerignore keeps caches and workbooks out.
COPY . .

# Default: the API. settle-cron overrides the start command.
CMD ["python", "-m", "uvicorn", "settle.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
