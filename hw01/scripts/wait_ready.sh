#!/usr/bin/env bash
# Завершается ТОЛЬКО когда стенд готов принимать запросы (иначе — ненулевой код по таймауту):
#   кластер Patroni собран (лидер + 2 реплики), все 4 БД созданы миграциями,
#   пул соединений отвечает на запись (6432) и на чтение (6433).
# Зависит только от docker compose и curl: проверки SQL выполняются внутри контейнеров (psql есть в образе узла),
# поэтому Python на хосте не нужен.
set -uo pipefail
cd "$(dirname "$0")/.."

TIMEOUT="${HW01_WAIT_TIMEOUT:-600}"
API="${HW01_PATRONI_API:-http://localhost:8008}"
DEADLINE=$(( $(date +%s) + TIMEOUT ))
DBS="crm_service_db catalog_service_db pos_service_db wms_service_db"

psql_in() {  # psql_in <dsn> <sql> — внутри контейнера pg1, по сети compose
    docker compose exec -T pg1 psql "$1" -v ON_ERROR_STOP=1 -Atc "$2" 2>&1
}

step() {  # step <название> <команда проверки>; печатает прогресс, ждёт до таймаута
    local name="$1"; shift
    local last="" out
    while true; do
        if out=$("$@" 2>&1); then
            echo "  ok  ${name}: ${out}"
            return 0
        fi
        out=$(printf '%s' "$out" | head -1 | cut -c1-150)
        if [ "$out" != "$last" ]; then echo "  ... ${name}: ${out}"; last="$out"; fi
        if [ "$(date +%s)" -gt "$DEADLINE" ]; then echo "ТАЙМАУТ ${TIMEOUT} c на шаге «${name}»" >&2; exit 1; fi
        sleep 3
    done
}

check_patroni() {
    local json leaders ready
    json=$(curl -s -m 5 "${API%/}/cluster") || { echo "REST API Patroni не отвечает"; return 1; }
    leaders=$(printf '%s' "$json" | grep -o '"role": *"leader"' | wc -l | tr -d ' ')
    # реплика готова: состояние running (Patroni <= 3.0.0) или streaming (новее) и нулевой лаг
    ready=$(printf '%s' "$json" | python3 -c 'import json,sys
ms = json.load(sys.stdin)["members"]
print(sum(1 for m in ms if m["role"] != "leader" and m["state"] in ("running", "streaming") and m.get("lag", 0) in (0, None)))' 2>/dev/null) || ready=$(printf '%s' "$json" | grep -o '"lag": *0' | wc -l | tr -d ' ')
    if [ "$leaders" != "1" ] || [ "${ready:-0}" -lt 2 ]; then echo "кластер не собран: лидеров ${leaders}, готовых реплик ${ready:-0}"; return 1; fi
    echo "лидер 1, реплик ${ready}"
}

check_databases() {
    local have db
    have=$(psql_in "host=haproxy port=5000 user=postgres password=postgres dbname=postgres" "select datname from pg_database") || { echo "лидер недоступен через HAProxy"; return 1; }
    for db in $DBS; do
        printf '%s\n' "$have" | grep -qx "$db" || { echo "нет базы ${db} (идёт инициализация)"; return 1; }
    done
    echo "4 базы созданы"
}

check_pool() {  # check_pool <хост пула> <порт> <ожидаемый in_recovery>
    local db out
    for db in $DBS; do
        out=$(psql_in "host=$1 port=$2 user=checker password=checker dbname=${db}" \
              "select pg_is_in_recovery()::text || ' ' || (select count(*) from information_schema.views where table_schema = 'contract')") || { echo "${db} через :$2 недоступна"; return 1; }
        case "$out" in
            "$3 0") echo "${db} через :$2: представлений contract нет"; return 1 ;;
            "$3 "*) ;;
            *) echo "${db} через :$2: ожидали in_recovery=$3, получили: ${out}"; return 1 ;;
        esac
    done
    echo "пул :$2 отвечает ($( [ "$3" = "true" ] && echo реплика || echo лидер ))"
}

step "patroni"     check_patroni
step "базы"        check_databases
step "пул запись"  check_pool pgbouncer-rw 6432 false
step "пул чтение"  check_pool pgbouncer-ro 6433 true
echo "стенд готов"
