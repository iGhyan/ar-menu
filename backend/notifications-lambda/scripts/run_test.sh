#!/usr/bin/env bash
# scripts/run_tests.sh
# Run pytest with coverage for notifications-lambda
# Usage: ./scripts/run_tests.sh [--html]   (--html generates htmlcov/ report)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${ROOT}"

# ── Install test deps if needed ───────────────────────────────────────────────
if ! python -c "import pytest" 2>/dev/null; then
  echo "📦  Installing test dependencies..."
  pip install -r requirements.txt --quiet
fi

# ── Run ───────────────────────────────────────────────────────────────────────
EXTRA_ARGS=""
if [[ "${1:-}" == "--html" ]]; then
  EXTRA_ARGS="--cov-report=html"
fi

echo "🧪  Running tests..."
python -m pytest tests/ \
  -v \
  --tb=short \
  --cov=handler \
  --cov-report=term-missing \
  --cov-fail-under=75 \
  ${EXTRA_ARGS}

echo ""
echo "✅  All tests passed."