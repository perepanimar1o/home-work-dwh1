# Генератор данных в контейнере: запасной путь, если на хосте нет Python с psycopg2 (см. scripts/seed.sh).
FROM python:3.12-slim
RUN pip install --no-cache-dir psycopg2-binary
WORKDIR /app
