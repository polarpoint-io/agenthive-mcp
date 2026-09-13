.PHONY: help install dev test lint format run run-http docker docker-run clean

IMAGE ?= agenthive-mcp:latest

help:
	@echo "Targets:"
	@echo "  install     Install the package"
	@echo "  dev         Install with dev extras (ruff, pytest)"
	@echo "  test        Run pytest"
	@echo "  lint        Run ruff"
	@echo "  format      Run ruff --fix"
	@echo "  run         Run the server on stdio"
	@echo "  run-http    Run the server on HTTP/SSE (port 8000)"
	@echo "  docker      Build the Docker image ($(IMAGE))"
	@echo "  docker-run  Run the Docker image in HTTP mode on :8000"
	@echo "  clean       Remove build and cache artefacts"

install:
	pip install -e .

dev:
	pip install -e .[dev]

test:
	pytest -q

lint:
	ruff check src tests

format:
	ruff check --fix src tests

run:
	TRANSPORT=stdio agenthive-mcp

run-http:
	TRANSPORT=http HTTP_PORT=8000 agenthive-mcp

docker:
	docker build -t $(IMAGE) .

docker-run:
	docker run --rm \
		-e AGENTHIVE_URL \
		-e AGENTHIVE_TOKEN \
		-e AGENTHIVE_TEAM_ID \
		-e TRANSPORT=http \
		-p 8000:8000 \
		$(IMAGE)

clean:
	rm -rf build dist *.egg-info .pytest_cache .ruff_cache .mypy_cache
	find . -type d -name __pycache__ -exec rm -rf {} +
