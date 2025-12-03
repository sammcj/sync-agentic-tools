.PHONY: help install dev-install venv test test-cov lint format check run clean

# Default target - show help
help:
	@echo "Available targets:"
	@echo "  make install      - Create venv and install production dependencies"
	@echo "  make dev-install  - Create venv and install with dev dependencies"
	@echo "  make venv         - Create and activate virtual environment"
	@echo "  make test         - Run tests"
	@echo "  make test-cov     - Run tests with coverage report"
	@echo "  make lint         - Run linting checks"
	@echo "  make format       - Auto-format code"
	@echo "  make check        - Run linting and tests"
	@echo "  make run          - Run the application"
	@echo "  make clean        - Remove generated files and venv"

# Create virtual environment and install production dependencies
install: venv
	@echo "Installing production dependencies..."
	@. .venv/bin/activate && uv pip install -e .

# Create virtual environment and install with dev dependencies
dev-install: venv
	@echo "Installing dev dependencies..."
	@. .venv/bin/activate && uv pip install -e ".[dev]"

# Create virtual environment
venv:
	@if [ ! -d .venv ]; then \
		echo "Creating virtual environment..."; \
		uv venv .venv; \
	else \
		echo "Virtual environment already exists"; \
	fi

# Run tests
test:
	@echo "Running tests..."
	@. .venv/bin/activate && pytest tests/ -v

# Run tests with coverage
test-cov:
	@echo "Running tests with coverage..."
	@. .venv/bin/activate && pytest tests/ --cov=sync_agentic_tools --cov-report=term-missing

# Run linting checks
lint:
	@echo "Running linting checks..."
	@. .venv/bin/activate && ruff check src/ tests/ --fix

# Check types
check-types:
	@echo "Checking types..."
	@. .venv/bin/activate && uvx ty check

# Auto-format code
format:
	@echo "Formatting code..."
	@. .venv/bin/activate && ruff format src/ tests/
	@. .venv/bin/activate && ruff check --fix src/ tests/
	@echo "Code formatted successfully"

# Run both linting and tests
check: lint test
	@echo "All checks passed!"

# Run the application
run:
	@. .venv/bin/activate && sync-agentic-tools

# Clean up generated files and venv
clean:
	@echo "Cleaning up..."
	@rm -rf .venv
	@rm -rf .pytest_cache
	@rm -rf .ruff_cache
	@rm -rf src/*.egg-info
	@rm -rf dist build
	@find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	@find . -type f -name "*.pyc" -delete
	@echo "Cleanup complete"
