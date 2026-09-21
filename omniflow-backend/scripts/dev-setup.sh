#!/usr/bin/env bash
# scripts/dev-setup.sh
# One-command dev environment bootstrap. Run once after cloning the repo.
# Tested on: macOS 14+, Ubuntu 22.04+, WSL2

set -euo pipefail

echo "🚀 OmniFlow AI — Dev Environment Setup"
echo "======================================="

# 1. Copy env file
if [ ! -f .env ]; then
  cp .env.example .env
  echo "✅ Created .env from .env.example — please fill in required values."
else
  echo "⚠️  .env already exists — skipping copy."
fi

# 2. Create Python virtual environment
if [ ! -d .venv ]; then
  python3.12 -m venv .venv
  echo "✅ Created Python 3.12 virtual environment."
fi

# Activate
source .venv/bin/activate

# 3. Install dependencies
echo "📦 Installing Python dependencies..."
pip install --upgrade pip
pip install -e ".[dev]"
echo "✅ Dependencies installed."

# 4. Start infrastructure
echo "🐳 Starting local infrastructure (Docker Compose)..."
docker compose up -d --wait
echo "✅ Infrastructure started."

# 5. Wait and create Kafka topics
echo "⏳ Waiting for Redpanda to be ready..."
sleep 5
docker exec omniflow-redpanda bash /infra/redpanda/create-topics.sh 2>/dev/null || \
  echo "⚠️  Topics script needs to be copied to container manually."

# 6. Run database migrations
echo "🗄️  Running database migrations..."
alembic upgrade head
echo "✅ Database migrations complete."

# 7. Install pre-commit hooks
echo "🔧 Installing pre-commit hooks..."
pre-commit install
echo "✅ Pre-commit hooks installed."

echo ""
echo "🎉 Dev environment ready!"
echo ""
echo "Services:"
echo "  API Gateway:      http://localhost:8000/docs"
echo "  PgAdmin:          http://localhost:5050  (run: docker compose --profile tools up)"
echo "  Redpanda Console: http://localhost:8080  (run: docker compose --profile tools up)"
echo "  MinIO Console:    http://localhost:9021"
echo "  Qdrant:           http://localhost:6333"
echo ""
echo "Run the gateway:"
echo "  uvicorn src.gateway.main:app --reload --host 0.0.0.0 --port 8000"
