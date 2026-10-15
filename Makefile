# WTI News Alpha — comandos del proyecto (CLAUDE.md §6.1)
.PHONY: setup check lint format typecheck test sources-check ingest build-features build-labels train evaluate report

UV ?= uv

setup:
	$(UV) sync --python 3.12
	$(UV) run pre-commit install

check: lint typecheck test

lint:
	$(UV) run ruff check src tests scripts
	$(UV) run ruff format --check src tests scripts

format:
	$(UV) run ruff check --fix src tests scripts
	$(UV) run ruff format src tests scripts

typecheck:
	$(UV) run mypy

test:
	$(UV) run pytest

sources-check:
	$(UV) run wna sources check

ingest:
	$(UV) run wna ingest

build-features:
	$(UV) run wna build-features

build-labels:
	$(UV) run wna build-labels

train:
	$(UV) run wna train

evaluate:
	$(UV) run wna evaluate

report:
	$(UV) run wna report
