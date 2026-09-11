# Image for the Railway services (settle-api, settle-cron). One image, two
# start commands (see .railway/railway.ts). Explicit on purpose: the Nixpacks
# Python builder installed the `api` extra outside its runtime venv and the
# container started with `uvicorn: command not found`.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first (cache-friendly): metadata + the package itself.
COPY pyproject.toml ./
COPY src ./src
RUN pip install -e ".[api]"

# Everything the pipeline reads at runtime: config/, scripts/, db/schema.sql,
# settlements/ (SNR cross-checks). .dockerignore keeps caches and workbooks out.
COPY . .

# Default: the API. settle-cron overrides the start command.
CMD ["python", "-m", "uvicorn", "settle.api.app:app", "--host", "0.0.0.0", "--port", "8000"]
