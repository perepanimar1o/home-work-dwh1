#!/usr/bin/env bash
# Эксперимент Э5: теряются ли ПОДТВЕРЖДЁННЫЕ транзакции при падении лидера?
#   1) запускаем write-loop (4 потока) в лидера через HAProxy;
#   2) на 8-й секунде кратко отключаем обе реплики от docker-сети (имитация отставания реплик);
#   3) пока реплики отрезаны, убиваем лидера (docker kill) и сразу возвращаем реплики в сеть;
#   4) ждём failover и проверяем: все ли id, подтверждённые клиенту (успешный COMMIT), есть на новом лидере.
# Режим репликации (async/sync) берётся из динамической конфигурации Patroni (synchronous_mode).
# Использование: ./scripts/failover_loss_demo.sh [пауза_между_отключением_и_убийством_сек, по умолчанию 0.5]
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
NET=${COMPOSE_NETWORK:-hw01_default}
GAP=${1:-0.5}
ts() { $PY -c "import datetime;t=datetime.datetime.now(datetime.timezone.utc);print(t.strftime('%H:%M:%S.')+f'{t.microsecond//1000:03d}')"; }

api() { for p in 8008 8009 8010; do curl -s -m 2 "localhost:$p/$1" && return 0; done; return 1; }
LEADER=$(api cluster | $PY -c "import json,sys; print([m['name'] for m in json.load(sys.stdin)['members'] if m['role']=='leader'][0])")
REPLICAS=$(api cluster | $PY -c "import json,sys; print(' '.join(m['name'] for m in json.load(sys.stdin)['members'] if m['role']!='leader'))")
SYNC=$(api config | $PY -c "import json,sys; print(json.load(sys.stdin).get('synchronous_mode', False))")
echo "synchronous_mode=$SYNC; лидер=$LEADER; реплики=$REPLICAS; пауза между отключением реплик и убийством лидера = ${GAP} с"

ACKS=$(mktemp)
$PY scripts/probe_write.py --seconds 70 --threads 4 --acks "$ACKS" > "$ACKS.out" 2>&1 &
PID=$!
sleep 8
for r in $REPLICAS; do echo "$(ts) UTC: docker network disconnect $NET hw01-$r-1"; docker network disconnect "$NET" "hw01-$r-1"; done
sleep "$GAP"
echo "$(ts) UTC: docker kill hw01-$LEADER-1   (лидер убит, реплики всё ещё отрезаны)"; docker kill "hw01-$LEADER-1" >/dev/null
for r in $REPLICAS; do echo "$(ts) UTC: docker network connect $NET hw01-$r-1"; docker network connect "$NET" "hw01-$r-1"; done
wait $PID
echo "--- итог write-loop:"; cat "$ACKS.out"
echo "--- проверка на новом лидере:"
$PY scripts/probe_write.py --verify "$ACKS" || true
rm -f "$ACKS" "$ACKS.out"
