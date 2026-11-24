# Warframe Profile — development, packaging, and installation tasks.
#
#   make install        # set up the Poetry virtualenv + systemd user service
#   make build-linux    # build dist/linux/warframe-profile (one-file executable)
#   make install-linux  # build and install it to $(BINDIR) (default ~/.local/bin)
#   make build-windows  # cross-build (and zip) dist/windows/warframe-profile.exe via Wine
#   make start          # start the systemd user service (also stop/restart)
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

#: systemd user service. `make install` installs it against the Poetry
#: virtualenv, while `make install-linux` installs it against $(BINDIR).
SYSTEMCTL        ?= systemctl --user
SYSTEMD_USER_DIR ?= $(HOME)/.config/systemd/user
SERVICE_NAME     ?= warframe-profile.service
SERVICE_UNIT_SRC := packaging/$(SERVICE_NAME)
SERVICE_FILE     := $(SYSTEMD_USER_DIR)/$(SERVICE_NAME)
SERVICE_EXEC     ?= $(BINDIR)/$(BIN_NAME) serve
SERVICE_WORKDIR  ?= $(HOME)

#: Windows cross-build settings (used by `make build-windows`).
#: The default installs 64-bit Python into a local Wine prefix on first use.
WINEPREFIX         ?= $(CURDIR)/.wine-build
WIN_PYTHON_NAME    ?= Python312
WIN_PYTHON_VERSION ?= 3.12.10
WIN_PYTHON_DIR     ?= C:\$(WIN_PYTHON_NAME)
WIN_PYTHON         ?= $(WIN_PYTHON_DIR)\python.exe
WIN_PYTHON_EXE     ?= $(WINEPREFIX)/drive_c/$(WIN_PYTHON_NAME)/python.exe
WIN_INSTALLER      ?= $(WINEPREFIX)/python-$(WIN_PYTHON_VERSION)-amd64.exe

.PHONY: help install uninstall update run lint format check test clean clean-data \
	install-service uninstall-service start stop restart \
	build-deps build-linux install-linux uninstall-linux wine-setup build-windows clean-build clean-wine

help: ## Show this help
	@awk 'BEGIN {FS = ":.*## "; printf "\nUsage: make <target> [ARGS=...]\n\nTargets:\n"} \
		/^[a-zA-Z_-]+:.*## / {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}' $(MAKEFILE_LIST)
	@printf "\nExamples:\n"
	@printf "  make run ARGS='ducats --refresh'\n"
	@printf "  make run ARGS='craft \"Acceltra Prime\" --depth 3'\n"
	@printf "  make install-linux\n"
	@printf "  make start\n"
	@printf "  make build-windows\n\n"

install: ## Install the dev environment and the systemd user service
	$(POETRY) install --extras dev
	@if command -v systemctl >/dev/null 2>&1; then \
		$(MAKE) --no-print-directory install-service \
			SERVICE_EXEC="$$($(POETRY) env info -p)/bin/python -m warframe_profile serve" \
			SERVICE_WORKDIR="$(CURDIR)"; \
	else \
		echo "systemctl not found; skipping systemd service installation" >&2; \
	fi

uninstall: uninstall-service ## Remove the systemd user service (keeps the Poetry env)

install-service: ## Install and enable the systemd user service
	@command -v systemctl >/dev/null 2>&1 || { \
		echo "error: systemctl not found; the service targets need systemd" >&2; \
		exit 1; \
	}
	install -d "$(SYSTEMD_USER_DIR)"
	sed -e 's|@SERVICE_EXEC@|$(SERVICE_EXEC)|g' \
		-e 's|@SERVICE_WORKDIR@|$(SERVICE_WORKDIR)|g' \
		"$(SERVICE_UNIT_SRC)" > "$(SERVICE_FILE)"
	$(SYSTEMCTL) daemon-reload
	$(SYSTEMCTL) enable "$(SERVICE_NAME)"
	$(SYSTEMCTL) restart "$(SERVICE_NAME)"
	@printf '\nInstalled %s\n' "$(SERVICE_FILE)"

uninstall-service: ## Stop and remove the systemd user service
	@if command -v systemctl >/dev/null 2>&1; then \
		$(SYSTEMCTL) disable --now "$(SERVICE_NAME)" >/dev/null 2>&1 || true; \
	fi
	rm -f "$(SERVICE_FILE)"
	@if command -v systemctl >/dev/null 2>&1; then \
		$(SYSTEMCTL) daemon-reload; \
	fi

start: ## Start the systemd user service
	$(SYSTEMCTL) start "$(SERVICE_NAME)"

stop: ## Stop the systemd user service
	$(SYSTEMCTL) stop "$(SERVICE_NAME)"

restart: ## Restart the systemd user service
	$(SYSTEMCTL) restart "$(SERVICE_NAME)"

update: ## Download and merge the latest game data into the per-user data dir
	$(POETRY) run warframe update

run: ## Run the CLI, e.g. make run ARGS='ducats'
	$(POETRY) run warframe $(ARGS)

lint: ## Check code style with ruff
	$(POETRY) run ruff check .

format: ## Auto-format the code with ruff
	$(POETRY) run ruff format .
	$(POETRY) run ruff check --fix .

check: ## Verify formatting, lint, and types without modifying files (CI-friendly)
	$(POETRY) run ruff format --check .
	$(POETRY) run ruff check .
	$(POETRY) run pyright

test: ## Run the test suite with pytest
	$(POETRY) run pytest

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

install-linux: build-linux ## Build and install the Linux executable and systemd service
	install -d "$(DESTDIR)$(BINDIR)"
	install -m 755 "$(DIST)/linux/$(BIN_NAME)" "$(DESTDIR)$(BINDIR)/$(BIN_NAME)"
	@printf '\nInstalled %s\n' "$(DESTDIR)$(BINDIR)/$(BIN_NAME)"
	@if [ -z "$(DESTDIR)" ]; then \
		$(MAKE) --no-print-directory install-service \
			SERVICE_EXEC="$(BINDIR)/$(BIN_NAME) serve" \
			SERVICE_WORKDIR="$(HOME)"; \
	else \
		echo "DESTDIR set; skipping systemd service installation"; \
	fi

uninstall-linux: ## Remove the installed Linux executable and systemd service
	@if [ -z "$(DESTDIR)" ]; then \
		$(MAKE) --no-print-directory uninstall-service; \
	fi
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

build-windows: wine-setup ## Cross-build and zip dist/windows/warframe-profile.exe (requires Wine)
	WINEPREFIX="$(WINEPREFIX)" WINEDEBUG=-all $(WINE) '$(WIN_PYTHON)' \
		-m PyInstaller --clean --noconfirm \
		--distpath "$(DIST)/windows" --workpath "$(BUILD)/windows" warframe.spec
	@command -v zip >/dev/null || { \
		echo "error: zip not found; install zip to package the Windows executable" >&2; \
		exit 1; \
	}
	@rm -f "$(DIST)/windows/$(BIN_NAME).zip"
	cd "$(DIST)/windows" && zip -q -9 "$(BIN_NAME).zip" "$(BIN_NAME).exe"
	@printf '\nBuilt %s\nPackaged %s\n' \
		"$(DIST)/windows/$(BIN_NAME).exe" \
		"$(DIST)/windows/$(BIN_NAME).zip"

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
