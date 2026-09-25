#!/usr/bin/env bash
set -Eeuo pipefail

maps_data_folder="${MAPS_SCRAPER_DATA_FOLDER:-/var/data/maps-scraper}"
mkdir -p "$maps_data_folder"

echo "Starting bundled Google Maps scraper with data folder: $maps_data_folder"
# Chromium workers are memory-heavy. Keep the bundled service to one worker
# and one browser process so concurrent/manual jobs cannot multiply browsers.
/usr/local/bin/google-maps-scraper \
  -web \
  -addr :8080 \
  -c 1 \
  -browser-pool-size 1 \
  -pages-per-browser 1 \
  -data-folder "$maps_data_folder" &
scraper_pid=$!
app_pid=""

cleanup() {
  trap - EXIT TERM INT
  if [[ -n "$app_pid" ]]; then
    kill -TERM "$app_pid" 2>/dev/null || true
  fi
  kill -TERM "$scraper_pid" 2>/dev/null || true
  if [[ -n "$app_pid" ]]; then
    wait "$app_pid" 2>/dev/null || true
  fi
  wait "$scraper_pid" 2>/dev/null || true
}
trap cleanup EXIT
trap 'exit 143' TERM
trap 'exit 130' INT

# Don't start discovery until the local scraper API is ready.
python - <<'PY'
import time
import urllib.error
import urllib.request

url = "http://127.0.0.1:8080/api/v1/jobs"
deadline = time.monotonic() + 90
last_error = None
while time.monotonic() < deadline:
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            if 200 <= response.status < 300:
                break
    except (OSError, urllib.error.URLError) as exc:
        last_error = exc
    time.sleep(1)
else:
    raise SystemExit(f"Maps scraper did not become ready at {url}: {last_error}")
PY

echo "Bundled Google Maps scraper is ready at http://127.0.0.1:8080"
echo "Starting leadgen web app on port ${PORT:-10000}"
uvicorn leadgen.dashboard:app --host 0.0.0.0 --port "${PORT:-10000}" &
app_pid=$!

# If either child exits, stop the container so Render can restart the service.
set +e
wait -n "$scraper_pid" "$app_pid"
status=$?
set -e
exit "$status"
