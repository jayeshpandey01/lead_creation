# Reuse the upstream scraper's compiled binary and its matching Playwright
# browser/runtime files, then run it beside the Python app in one Render service.
FROM docker.io/gosom/google-maps-scraper:latest AS maps-scraper

FROM python:3.12-slim

ENV PLAYWRIGHT_BROWSERS_PATH=/opt/browsers \
    PLAYWRIGHT_DRIVER_PATH=/opt/ms-playwright-go \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# Runtime libraries required by the upstream Playwright Chromium image.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates \
    libnss3 \
    libnspr4 \
    libatk1.0-0 \
    libatk-bridge2.0-0 \
    libcups2 \
    libdrm2 \
    libdbus-1-3 \
    libxkbcommon0 \
    libatspi2.0-0 \
    libx11-6 \
    libxcomposite1 \
    libxdamage1 \
    libxext6 \
    libxfixes3 \
    libxrandr2 \
    libgbm1 \
    libpango-1.0-0 \
    libcairo2 \
    libasound2 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=maps-scraper /usr/bin/google-maps-scraper /usr/local/bin/google-maps-scraper
COPY --from=maps-scraper /opt/browsers /opt/browsers
COPY --from=maps-scraper /opt/ms-playwright-go /opt/ms-playwright-go

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir --upgrade pip && pip install --no-cache-dir -e .

COPY config ./config
COPY queries.txt leads_input.csv ./
COPY scripts/start_render.sh ./scripts/start_render.sh
RUN chmod +x ./scripts/start_render.sh

CMD ["/app/scripts/start_render.sh"]
