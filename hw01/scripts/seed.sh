#!/usr/bin/env bash
# Генератор данных.  ./scripts/seed.sh --seed 42                    базовая загрузка (идемпотентно)
#                    ./scripts/seed.sh --seed 777 --receipts 500    догенерировать 500 чеков поверх данных
# Подключение: лидер кластера через HAProxy (localhost:5400), роль app. Переопределяется HW01_DB_*.
# Интерпретатор: $PYTHON, иначе python3/python, где импортируется psycopg2; если такого нет —
# генератор запускается в контейнере `seeder` (docker compose run), и от Python хоста ничего не требуется.
set -euo pipefail
cd "$(dirname "$0")/.."

PYBIN="${PYTHON:-}"
if [ -z "$PYBIN" ]; then
    for cand in python3 python; do
        if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import psycopg2" >/dev/null 2>&1; then
            PYBIN="$cand"
            break
        fi
    done
fi

if [ -n "$PYBIN" ]; then
    exec "$PYBIN" scripts/seed.py "$@"
fi

echo "[seed.sh] на хосте нет Python с psycopg2 — запускаю генератор в контейнере seeder" >&2
exec docker compose run --rm -T seeder "$@"
