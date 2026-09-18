FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential ffmpeg curl \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY README.md ./
COPY packages ./packages
COPY services ./services
COPY prompts ./prompts

RUN pip install --upgrade pip && pip install -e .

EXPOSE 8000

FROM base AS api
CMD ["uvicorn", "services.api:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS stt
RUN pip install -e '.[stt]'
CMD ["uvicorn", "services.stt:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS rag
RUN pip install -e '.[rag-models]'
CMD ["uvicorn", "services.rag:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS tts
RUN pip install -e '.[tts]'
CMD ["uvicorn", "services.tts:app", "--host", "0.0.0.0", "--port", "8000"]

FROM base AS monitoring
CMD ["uvicorn", "services.monitoring:app", "--host", "0.0.0.0", "--port", "8000"]
