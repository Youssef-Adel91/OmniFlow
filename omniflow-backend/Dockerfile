FROM python:3.11-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Build-only tools (compilers, dev headers) — never copied into the runtime
# stage below, so they don't bloat the final image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential libffi-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# The Compose stack has no GPU runtime. Avoid bundling CUDA into every
# worker — installed from a separate CPU-only index, pinned exactly, before
# requirements-lock.txt's resolver-driven install (which excludes torch —
# see that file's header comment for why).
RUN pip install --no-cache-dir torch==2.13.0+cpu --index-url https://download.pytorch.org/whl/cpu

# item 19 (launch readiness): exact pinned versions, not pyproject's ">="
# floors — a build today and a build next year get the identical graph.
COPY requirements-lock.txt ./
RUN pip install --no-cache-dir -r requirements-lock.txt

COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir --no-deps .

FROM python:3.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app

# Runtime-only shared libraries — no compilers, no -dev header packages.
# No libpq: the app uses asyncpg (a pure asyncio driver), never libpq/psycopg.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libmagic1 ffmpeg tesseract-ocr tesseract-ocr-ara \
    libpango-1.0-0 libpangocairo-1.0-0 libgdk-pixbuf2.0-0 shared-mime-info \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./
RUN useradd --create-home appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000
CMD ["uvicorn", "src.gateway.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
