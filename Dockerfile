FROM node:22-bookworm-slim AS assets
WORKDIR /build
COPY package.json package-lock.json ./
RUN npm ci --ignore-scripts
COPY tailwind.config.js postcss.config.js ./
COPY theme ./theme
COPY templates ./templates
COPY apps ./apps
RUN npm run build:css

FROM python:3.12-slim-bookworm AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends libmagic1 \
    && groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY --chown=app:app manage.py ./
COPY --chown=app:app config ./config
COPY --chown=app:app apps ./apps
COPY --chown=app:app templates ./templates
COPY --chown=app:app static ./static
COPY --from=assets --chown=app:app /build/static/css/main.css ./static/css/main.css
RUN mkdir -p /app/media /app/staticfiles /app/state && chown -R app:app /app/media /app/staticfiles /app/state
RUN DJANGO_DEBUG=true DJANGO_SECRET_KEY=build-only-static-key python manage.py collectstatic --noinput
USER app
EXPOSE 8000
CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000", "--workers", "2", "--timeout", "60", "--access-logfile", "-", "--access-logformat", "%(s)s %(b)s %(D)s", "--error-logfile", "-"]
