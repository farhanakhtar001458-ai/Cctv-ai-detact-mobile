# Smart Classroom AI - common tasks
SHELL := /bin/bash
PY ?= .venv/bin/python
PIP ?= .venv/bin/pip
BACKEND := backend
FRONTEND := frontend
PORT ?= 8000

.PHONY: help setup setup-vision dev api web build test test-backend test-frontend lint \
        check model benchmark demo-video clean docker

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-16s\033[0m %s\n", $$1, $$2}'

setup: ## Create the venv and install backend dependencies
	python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -r $(BACKEND)/requirements.txt
	$(PIP) install pytest httpx pytest-asyncio

setup-vision: ## Install PyTorch + Ultralytics (large download)
	$(PIP) install -r $(BACKEND)/requirements-vision.txt

api: ## Run the API (serves the built dashboard if present)
	cd $(BACKEND) && ../$(PY) -m uvicorn app.main:app --host 0.0.0.0 --port $(PORT) --reload

web: ## Run the Vite dev server (proxies /api to the API)
	cd $(FRONTEND) && npm install && npm run dev

dev: ## Reminder: run `make api` and `make web` in two terminals
	@echo "Terminal 1: make api    -> http://localhost:$(PORT)"
	@echo "Terminal 2: make web    -> http://localhost:5173"

build: ## Build the dashboard for production
	cd $(FRONTEND) && npm install && npm run build

test: test-backend test-frontend ## Run all tests

test-backend: ## Backend unit/integration tests
	cd $(BACKEND) && ../$(PY) -m pytest -q -o addopts=""

test-frontend: ## TypeScript typecheck + production build
	cd $(FRONTEND) && npm run typecheck && npm run build

lint: ## Ruff (backend) if installed
	cd $(BACKEND) && ../$(PY) -m ruff check app scripts tests || true

check: ## Environment doctor (warnings are not fatal)
	@$(PY) $(BACKEND)/scripts/check_env.py; code=$$?; \
	if [ $$code -eq 1 ]; then echo "(degraded: see warnings above; run 'make model' for phone detection)"; exit 0; fi; \
	exit $$code

model: ## Download/provision YOLO weights
	$(PY) $(BACKEND)/scripts/fetch_model.py

benchmark: ## Benchmark latency/FPS at several resolutions
	$(PY) $(BACKEND)/scripts/benchmark.py --source sim://classroom --frames 150 \
		--resolutions 640x360,960x540,1280x720

demo-video: ## Render a simulated classroom video for file-mode testing
	$(PY) $(BACKEND)/scripts/generate_demo_video.py --out samples/sim_classroom.mp4 --seconds 60

docker: ## Build and start with Docker Compose
	docker compose up --build -d

clean: ## Remove caches and build outputs (keeps data/ and node_modules)
	find $(BACKEND) -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf $(FRONTEND)/dist .pytest_cache
