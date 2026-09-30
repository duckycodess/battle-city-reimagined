.PHONY: format format-check lint typecheck test ci

format:
	uv run ruff format packages tests tools

format-check:
	uv run --locked ruff format --check packages tests tools

lint:
	uv run --locked ruff check packages tests tools

typecheck:
	uv run --locked mypy packages tests

test:
	uv run --locked pytest

ci: format-check lint typecheck test
