# ─── WebSocket SAM Project — Makefile ─────────────────────
.PHONY: install build test test-cov deploy-dev deploy-prod local clean

# ── Setup ──────────────────────────────────────────────────
install:
	pip install -r requirements-dev.txt

# ── Build (SAM) ────────────────────────────────────────────
build:
	sam build

# ── Tests ──────────────────────────────────────────────────
test:
	pytest tests/ -v

test-cov:
	pytest tests/ -v \
	  --cov=functions \
	  --cov-report=term-missing \
	  --cov-fail-under=70

# ── Local invoke (requires sam build first) ────────────────
local-connect:
	sam local invoke WsConnectFunction \
	  --event events/connect.json \
	  --env-vars events/env.json

local-disconnect:
	sam local invoke WsDisconnectFunction \
	  --event events/disconnect.json \
	  --env-vars events/env.json

local-message:
	sam local invoke WsMessageFunction \
	  --event events/message.json \
	  --env-vars events/env.json

# ── Deploy ─────────────────────────────────────────────────
deploy-dev:
	sam build && sam deploy --config-env dev

deploy-prod:
	sam build && sam deploy --config-env prod

# ── Cleanup ────────────────────────────────────────────────
clean:
	rm -rf .aws-sam/ __pycache__ .pytest_cache .coverage
