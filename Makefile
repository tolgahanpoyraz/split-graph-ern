.PHONY: help sync test test-all lint fmt typecheck build clean

help:
	@echo "Targets:"
	@echo "  sync       create the venv and install dev tools (uv)"
	@echo "  test       run tests (fast subset; skips slow regression)"
	@echo "  test-all   run all tests, including slow regression checks"
	@echo "  lint       ruff check"
	@echo "  fmt        ruff format"
	@echo "  typecheck  mypy on the package"
	@echo "  build      build the C++ tools in cpp/ (requires nauty)"
	@echo "  clean      remove build / cache artifacts"

sync:
	uv sync

test:
	uv run pytest

test-all:
	uv run pytest --run-slow

lint:
	uv run ruff check .

fmt:
	uv run ruff format .

typecheck:
	uv run mypy src

build:
	$(MAKE) -C cpp

clean:
	-$(MAKE) -C cpp clean
	rm -rf .venv .pytest_cache .mypy_cache .ruff_cache build dist *.egg-info
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
