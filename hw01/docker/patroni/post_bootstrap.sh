#!/bin/bash
# Выполняется Patroni ОДИН раз — на лидере сразу после initdb первого узла кластера.
# Создаёт роли, четыре отдельные БД (не схемы!), накатывает миграции и включает публикации CDC.
# Остальные узлы получают всё это через basebackup, повторно запускать ничего не нужно.
set -euo pipefail

export PGHOST=/var/run/postgresql PGUSER=postgres
PSQL="psql -v ON_ERROR_STOP=1 -q -o /dev/null"

echo "[post_bootstrap] роли"
$PSQL -d postgres <<'SQL'
CREATE ROLE app     LOGIN PASSWORD 'app';       -- владелец объектов, под ней приложение и негативные тесты
CREATE ROLE checker LOGIN PASSWORD 'checker';   -- чекер: только SELECT на схему contract
GRANT pg_monitor TO checker;                    -- видеть pg_stat_wal_receiver / pg_stat_replication (только чтение статистики)
SQL

for svc in crm catalog pos wms; do
    db="${svc}_service_db"
    echo "[post_bootstrap] база $db"
    $PSQL -d postgres -c "CREATE DATABASE ${db} OWNER app"
    for f in /migrations/common/000_common.sql /migrations/${svc}/*.sql; do
        echo "[post_bootstrap]   $(basename "$f")"
        PGOPTIONS='-c role=app' $PSQL -d "$db" -f "$f"
    done
    # publication на все таблицы создаёт суперпользователь
    $PSQL -d "$db" -f /migrations/common/999_cdc.sql
done
echo "[post_bootstrap] готово"
