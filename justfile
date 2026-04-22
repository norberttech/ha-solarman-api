set dotenv-load := true
set dotenv-required := false

# Default recipe: show the list of recipes.
default:
    @just --list

# Run the fast test suite (excludes tests/test_live.py).
test:
    .venv/bin/pytest tests/ --ignore=tests/test_live.py

# Run the live API end-to-end test. Requires SOLARMAN_* env vars.
test-live:
    .venv/bin/pytest tests/test_live.py -s

# Lint the integration + tests with ruff (nix-shell provided).
lint:
    ruff check custom_components tests

# Auto-format with ruff (nix-shell provided).
format:
    ruff format custom_components tests

# Type-check the integration with mypy (nix-shell provided).
typecheck:
    mypy custom_components/solarman_api

# Start the local Home Assistant container in the background.
ha-up:
    docker compose up -d

# Stop the local Home Assistant container (preserves ./ha-config).
ha-down:
    docker compose down

# Restart only the Home Assistant service (picks up integration code changes).
ha-restart:
    docker compose restart homeassistant

# Tail Home Assistant logs.
ha-logs:
    docker compose logs -f homeassistant

# Wipe ha-config/ and start a fresh Home Assistant (re-runs onboarding).
ha-reset:
    docker compose down
    rm -rf ha-config
    docker compose up -d
