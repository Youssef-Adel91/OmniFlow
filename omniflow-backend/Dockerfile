FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libpq-dev libmagic1 ffmpeg tesseract-ocr tesseract-ocr-ara \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 libffi-dev shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

# The Compose stack has no GPU runtime. Avoid bundling CUDA into every worker.
RUN pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir .

COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "src.gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
