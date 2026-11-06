# Warframe Profile — development and installation tasks.
#
#   make install        # set up the Poetry virtualenv (dev tools included)
#   make install-user   # expose the `warframe` command globally via pipx
#   make run ARGS=ducats
#   make check
#   make clean

SHELL := /bin/bash
.DEFAULT_GOAL := help

POETRY ?= poetry
PIPX   ?= pipx
ARGS   ?=

.PHONY: help install install-user uninstall-user update run lint format check clean clean-data

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "\nUsage: make <target> [ARGS=...]\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nExamples:\n"
	@printf "  make run ARGS='--ducats --refresh'\n"
	@printf "  make run ARGS='--craft \"Acceltra Prime\" --depth 3'\n\n"

install: ## Install the project into the Poetry virtualenv (dev tools included)
	$(POETRY) install --extras dev

install-user: ## Install the `warframe` command for the current user via pipx (editable)
	$(PIPX) install --editable --force .

uninstall-user: ## Remove the user-level `warframe` command
	$(PIPX) uninstall warframe-profile

update: ## Download and merge the latest game data into ~/.local/share/warframe-profile/export_db.json
	$(POETRY) run warframe update

run: ## Run the CLI, e.g. make run ARGS='--ducats'
	$(POETRY) run warframe $(ARGS)

lint: ## Check code style with ruff
	$(POETRY) run ruff check .

format: ## Auto-format the code with ruff
	$(POETRY) run ruff format .
	$(POETRY) run ruff check --fix .

check: ## Verify formatting and lint without modifying files (CI-friendly)
	$(POETRY) run ruff format --check .
	$(POETRY) run ruff check .

clean: ## Remove Python and ruff caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	rm -rf .ruff_cache

clean-data: ## Remove generated data files (export db + inventory cache)
	rm -f ~/.local/share/warframe-profile/*.json
