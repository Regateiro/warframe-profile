# Warframe Profile — development, packaging, and installation tasks.
#
#   make install        # set up the Poetry virtualenv (dev tools included)
#   make build-linux    # build dist/linux/warframe-profile (one-file executable)
#   make install-linux  # build and install it to $(BINDIR) (default ~/.local/bin)
#   make build-windows  # cross-build dist/windows/warframe-profile.exe via Wine
#   make run ARGS=ducats
#   make check
#   make clean

SHELL := /bin/bash
.DEFAULT_GOAL := help

POETRY ?= poetry
ARGS   ?=
WINE   ?= wine

DIST  ?= dist
BUILD ?= build

#: Name of the built executable (without the platform extension).
BIN_NAME ?= warframe-profile

#: Where `make install-linux` puts the binary. Override with BINDIR=... or
#: use DESTDIR for staged installs (e.g. packaging).
BINDIR  ?= $(HOME)/.local/bin
DESTDIR ?=

#: Windows cross-build settings (used by `make build-windows`).
#: The default installs 64-bit Python into a local Wine prefix on first use.
WINEPREFIX         ?= $(CURDIR)/.wine-build
WIN_PYTHON_NAME    ?= Python312
WIN_PYTHON_VERSION ?= 3.12.10
WIN_PYTHON_DIR     ?= C:\$(WIN_PYTHON_NAME)
WIN_PYTHON         ?= $(WIN_PYTHON_DIR)\python.exe
WIN_PYTHON_EXE     ?= $(WINEPREFIX)/drive_c/$(WIN_PYTHON_NAME)/python.exe
WIN_INSTALLER      ?= $(WINEPREFIX)/python-$(WIN_PYTHON_VERSION)-amd64.exe

.PHONY: help install update run lint format check clean clean-data \
	build-deps build-linux install-linux uninstall-linux wine-setup build-windows clean-build clean-wine

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "\nUsage: make <target> [ARGS=...]\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nExamples:\n"
	@printf "  make run ARGS='--ducats --refresh'\n"
	@printf "  make run ARGS='--craft \"Acceltra Prime\" --depth 3'\n"
	@printf "  make install-linux\n"
	@printf "  make build-windows\n\n"

install: ## Install the project into the Poetry virtualenv (dev tools included)
	$(POETRY) install --extras dev

update: ## Download and merge the latest game data into the per-user data dir
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

# ---------------------------------------------------------------------------
# Linux executable
# ---------------------------------------------------------------------------

build-deps: ## Install PyInstaller into the Poetry environment (used by `make build-linux`)
	@$(POETRY) run python -c 'import PyInstaller' 2>/dev/null || \
		$(POETRY) run python -m pip install --disable-pip-version-check -q pyinstaller

build-linux: build-deps ## Build a one-file Linux executable at dist/linux/warframe-profile
	$(POETRY) run python -m PyInstaller --clean --noconfirm \
		--distpath "$(DIST)/linux" --workpath "$(BUILD)/linux" warframe.spec
	@printf '\nBuilt %s\n' "$(DIST)/linux/$(BIN_NAME)"

install-linux: build-linux ## Build and install the Linux executable to $(BINDIR)
	install -d "$(DESTDIR)$(BINDIR)"
	install -m 755 "$(DIST)/linux/$(BIN_NAME)" "$(DESTDIR)$(BINDIR)/$(BIN_NAME)"
	@printf '\nInstalled %s\n' "$(DESTDIR)$(BINDIR)/$(BIN_NAME)"

uninstall-linux: ## Remove the installed Linux executable
	rm -f "$(DESTDIR)$(BINDIR)/$(BIN_NAME)"

# ---------------------------------------------------------------------------
# Windows executable (cross-build via Wine)
# ---------------------------------------------------------------------------

wine-setup: ## Install Windows Python + PyInstaller in the Wine prefix (used by `make build-windows`)
	@command -v "$(WINE)" >/dev/null || { \
		echo "error: wine not found; install Wine to cross-build the Windows executable" >&2; \
		exit 1; \
	}
	@if [ ! -x "$(WIN_PYTHON_EXE)" ]; then \
		command -v curl >/dev/null || { echo "error: curl not found" >&2; exit 1; }; \
		echo "Creating Wine prefix at $(WINEPREFIX) ..."; \
		WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) wineboot -u; \
		echo "Downloading Python $(WIN_PYTHON_VERSION) for Windows ..."; \
		curl -fsSL -o "$(WIN_INSTALLER)" \
			"https://www.python.org/ftp/python/$(WIN_PYTHON_VERSION)/python-$(WIN_PYTHON_VERSION)-amd64.exe"; \
		echo "Installing Python into $(WIN_PYTHON_DIR) ..."; \
		WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) "$(WIN_INSTALLER)" \
			/quiet InstallAllUsers=0 PrependPath=0 Include_pip=1 Include_test=0 \
			Include_launcher=0 'TargetDir=$(WIN_PYTHON_DIR)'; \
	fi
	@if ! WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) '$(WIN_PYTHON)' \
			-c 'import PyInstaller, prettytable' >/dev/null 2>&1; then \
		echo "Installing PyInstaller and project dependencies into the Wine Python ..."; \
		WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) '$(WIN_PYTHON)' \
			-m pip install --disable-pip-version-check -q --upgrade pip pyinstaller; \
		WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) '$(WIN_PYTHON)' \
			-m pip install --disable-pip-version-check -q .; \
	fi

build-windows: wine-setup ## Cross-build dist/windows/warframe-profile.exe (requires Wine)
	WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) '$(WIN_PYTHON)' \
		-m PyInstaller --clean --noconfirm \
		--distpath "$(DIST)/windows" --workpath "$(BUILD)/windows" warframe.spec
	@printf '\nBuilt %s\n' "$(DIST)/windows/$(BIN_NAME).exe"

# ---------------------------------------------------------------------------
# Cleanup
# ---------------------------------------------------------------------------

clean: ## Remove Python and ruff caches
	find . -type d -name __pycache__ -prune -exec rm -rf {} +
	find . -type f -name '*.pyc' -delete
	rm -rf .ruff_cache

clean-data: ## Remove generated data files (export db + inventory cache)
	rm -f "$$($(POETRY) run python -c 'from warframe_profile import DATA_DIR; print(DATA_DIR)')"/*.json

clean-build: ## Remove executable build output (dist/ and build/)
	rm -rf "$(BUILD)" "$(DIST)"

clean-wine: ## Remove the Wine cross-build prefix
	rm -rf "$(WINEPREFIX)"
