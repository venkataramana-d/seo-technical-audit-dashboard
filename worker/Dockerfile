# Always-on crawl worker (M2 T2.1) — runs `python -m worker` against Neon.
# Deploy target: Railway (or any container host). The Next.js frontend keeps
# deploying to Vercel separately; this image is only the background worker.
#
# Python 3.12 (not 3.14) for the widest Linux wheel availability; the code runs
# on 3.12. Playwright is installed with its matching Chromium + OS deps so the
# crawler's JS-render pass works.
FROM python:3.12-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install Python deps first (better layer caching), then the Chromium browser
# and its system libraries that Playwright needs.
COPY requirements.txt ./
RUN pip install -r requirements.txt \
    && playwright install --with-deps chromium

# App code (see .dockerignore — node_modules/.next/.git are excluded).
COPY . .

# The worker loop: claims queued crawl.start jobs and ticks scheduled crawls.
# Needs DATABASE_URL (Neon) in the environment. See DEPLOY-WORKER.md.
CMD ["python", "-m", "worker"]
