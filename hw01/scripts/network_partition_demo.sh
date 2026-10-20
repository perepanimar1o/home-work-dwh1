#!/usr/bin/env bash
# Эксперимент Э8: сетевой раздел. Лидер отключается от docker-сети (процесс жив, но изолирован).
# Проверяем: что делает изолированный узел, появился ли второй лидер, что предотвратило split brain.
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
NET=${COMPOSE_NETWORK:-hw01_default}
ts() { $PY -c "import datetime;t=datetime.datetime.now(datetime.timezone.utc);print(t.strftime('%H:%M:%S.')+f'{t.microsecond//1000:03d}')"; }
api() { for p in 8008 8009 8010; do curl -s -m 2 "localhost:$p/$1" && return 0; done; return 1; }
show() { api cluster | $PY -c "import json,sys;print(', '.join(f\"{m['name']}={m['role']}/{m['state']}\" for m in json.load(sys.stdin)['members']))"; }
LEADER=$(api cluster | $PY -c "import json,sys; print([m['name'] for m in json.load(sys.stdin)['members'] if m['role']=='leader'][0])")
echo "кластер до: $(show); изолируем лидера $LEADER"
W=$(mktemp); $PY scripts/probe_write.py --seconds 75 --threads 2 --sleep-ms 5 --acks "$W.acks" > "$W" 2>&1 &
PW=$!
sleep 10
echo "$(ts) UTC: docker network disconnect $NET hw01-$LEADER-1"; docker network disconnect "$NET" "hw01-$LEADER-1"
for i in 1 2 3 4 5 6 7 8 9; do
  sleep 4
  ISO=$(docker exec "hw01-$LEADER-1" psql -U postgres -Atc "select 'in_recovery='||pg_is_in_recovery()||', default_transaction_read_only='||current_setting('default_transaction_read_only')" 2>&1 | head -1 || true)
  WR=$(docker exec "hw01-$LEADER-1" psql -U postgres -d postgres -Atc "insert into probe_writes(worker, seq) values (99, $i) returning 'ЗАПИСЬ ПРИНЯТА изолированным узлом, id='||id" 2>&1 | head -1 || true)
  echo "$(ts) UTC: изолированный $LEADER: $ISO | попытка записи: $WR"
  echo "$(ts) UTC:   вид кластера снаружи: $(show 2>/dev/null || echo 'нет ответа')"
done
echo "$(ts) UTC: docker network connect $NET hw01-$LEADER-1"; docker network connect "$NET" "hw01-$LEADER-1"
wait $PW
echo "=== write-loop через HAProxy ==="; cat "$W"
echo "=== проверка потерь подтверждённых транзакций на новом лидере ==="; $PY scripts/probe_write.py --verify "$W.acks" || true
sleep 15; echo "кластер после возврата: $(show)"
rm -f "$W" "$W.acks"
