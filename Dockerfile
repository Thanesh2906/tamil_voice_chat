FROM python:3.12.5-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY jarvis ./jarvis
COPY prompts ./prompts
RUN pip install --no-cache-dir .
RUN useradd --create-home --uid 10001 jarvis && mkdir -p /app/data && chown -R jarvis:jarvis /app
USER jarvis
EXPOSE 8000
CMD ["uvicorn", "jarvis.app:app", "--host", "0.0.0.0", "--port", "8000"]
