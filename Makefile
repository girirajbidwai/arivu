# ============================================
# Arivu — Makefile
# One-command dev environment
# ============================================

.PHONY: dev voice brain dashboard-api dashboard-web test clean

# --- Full stack local dev ---
dev:
	@echo "Starting all Arivu services..."
	@make voice & make brain & make dashboard-api & make dashboard-web
	@wait

# --- Individual services ---
voice:
	@echo "🎙️  Starting Voice Pipeline (port 8000)..."
	cd voice && uv run uvicorn voice.main:app --host 0.0.0.0 --port 8000 --reload

brain:
	@echo "🧠 Starting Brain Service (port 8002)..."
	cd voice && uv run uvicorn voice.brain.main:app --host 0.0.0.0 --port 8002 --reload

dashboard-api:
	@echo "📡 Starting Dashboard API (port 8001)..."
	cd dashboard-api && uv run uvicorn dashboard_api.main:app --host 0.0.0.0 --port 8001 --reload

dashboard-web:
	@echo "🖥️  Starting Dashboard Web (port 3000)..."
	cd dashboard-web && pnpm dev

# --- Testing ---
test:
	@echo "Running all tests..."
	cd voice && uv run pytest tests/ -v
	cd dashboard-api && uv run pytest tests/ -v

test-voice:
	cd voice && uv run pytest tests/ -v

test-api:
	cd dashboard-api && uv run pytest tests/ -v

# --- Utilities ---
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true

ngrok:
	@echo "Starting ngrok tunnel for Twilio..."
	ngrok http 8000 --domain=$(NGROK_DOMAIN)

demo:
	@echo "=== ARIVU DEMO MODE ==="
	@echo "1. Voice pipeline: http://localhost:8000"
	@echo "2. Brain service:  http://localhost:8002"
	@echo "3. Dashboard API:  http://localhost:8001"
	@echo "4. Dashboard Web:  http://localhost:3000"
	@echo "5. ngrok tunnel:   https://$(NGROK_DOMAIN)"
	@echo ""
	@make dev
