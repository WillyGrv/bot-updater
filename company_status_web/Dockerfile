# Image officielle Playwright — navigateurs Chromium déjà installés avec leurs
# dépendances système, version alignée avec requirements.txt (playwright==1.47.0).
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PORT=10000
EXPOSE 10000

CMD gunicorn --bind 0.0.0.0:${PORT} --timeout 600 --workers 1 app:app
