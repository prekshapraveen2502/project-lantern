# Part 4 item 6: the grounded answering service, replay mode. Code and params only; every data path is mounted
# read-only by deploy/compose.yaml (decision Q8 a), so no data-room file and no key is ever in the image.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 LANTERN_LLM_MODE=replay ANONYMIZED_TELEMETRY=False
WORKDIR /app

COPY services/qa/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY params.yaml ./
COPY config/ config/
COPY src/ src/
COPY services/ services/
# src/llm/client.py creates data/llm_cache and data/embed_cache on import; the service then points it at the mounted
# data/qa_cache (src/qa/answer.py use_qa_caches), so these two stay empty.
RUN useradd --create-home lantern && mkdir -p data/llm_cache data/embed_cache && chown -R lantern /app
USER lantern

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=5s --retries=6 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)"
CMD ["uvicorn", "services.qa.app:app", "--host", "0.0.0.0", "--port", "8000"]
