# The hosted demo: the public pages only (receipt_iq.web), with no database, Redis or
# provider keys, so nothing a visitor does can spend a quota.

FROM node:24-alpine AS screens
WORKDIR /app/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
COPY pyproject.toml ./
COPY src/ src/
RUN pip install --no-cache-dir .
COPY leaderboard.json ./
COPY demo/ demo/
COPY --from=screens /app/frontend/dist frontend/dist
RUN useradd --create-home app
USER app
EXPOSE 8000
# Render sets PORT. Its proxy is the only way in, so the visitor address it forwards is
# trusted, and the rate limit counts visitors rather than the proxy.
CMD ["sh", "-c", "exec uvicorn receipt_iq.web:create_demo_app --factory --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
