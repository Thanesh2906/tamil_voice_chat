FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY packages ./packages
COPY services ./services
COPY prompts ./prompts

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000
CMD ["uvicorn", "services.api:app", "--host", "0.0.0.0", "--port", "8000"]
