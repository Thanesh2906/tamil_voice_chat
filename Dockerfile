FROM python:3.12.5-slim

ENV PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl ffmpeg \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml README.md ./
COPY packages ./packages
COPY services ./services
COPY prompts ./prompts
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 jarvis && chown -R jarvis:jarvis /app
USER jarvis
EXPOSE 8000
CMD ["uvicorn", "services.api:app", "--host", "0.0.0.0", "--port", "8000"]
