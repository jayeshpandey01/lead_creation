#!/usr/bin/env bash
set -euo pipefail

DOCKER_BIN="$(command -v docker || true)"
if [[ -z "$DOCKER_BIN" && -x /Applications/Docker.app/Contents/Resources/bin/docker ]]; then
  DOCKER_BIN=/Applications/Docker.app/Contents/Resources/bin/docker
fi

if [[ -z "$DOCKER_BIN" ]]; then
  echo "Docker CLI was not found. Start a new terminal after installing Docker Desktop, or add its CLI directory to PATH." >&2
  exit 1
fi

# Docker Desktop bundles its credential helper beside the CLI. When the CLI
# was installed through Docker.app but that directory is not on PATH, image
# pulls otherwise fail with "docker-credential-desktop not found".
DOCKER_BIN_DIR="$(dirname "$DOCKER_BIN")"
if [[ -x "$DOCKER_BIN_DIR/docker-credential-desktop" ]]; then
  export PATH="$DOCKER_BIN_DIR:$PATH"
fi

if ! "$DOCKER_BIN" info >/dev/null 2>&1; then
  echo "Docker is installed but not running. Start Docker Desktop, then run this script again." >&2
  exit 1
fi

if "$DOCKER_BIN" container inspect leadgen-maps-scraper >/dev/null 2>&1; then
  echo "Maps scraper already exists; starting it."
  "$DOCKER_BIN" start leadgen-maps-scraper
else
  "$DOCKER_BIN" run --name leadgen-maps-scraper \
    -p 8080:8080 \
    -v leadgen-maps-data:/gmapsdata \
    gosom/google-maps-scraper \
    -web -addr :8080 -data-folder /gmapsdata
fi
