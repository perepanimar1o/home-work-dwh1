#!/usr/bin/env bash
# Эксперимент Э6: отказ DCS (etcd из трёх узлов).
#   t= 10 c  останавливаем ОДИН etcd  -> кворум (2 из 3) жив, кластер не должен заметить
#   t= 40 c  останавливаем ВТОРОЙ etcd -> кворума нет, Patroni потеряет возможность обновлять ключ лидера
#   t= 80 c  возвращаем оба etcd       -> смотрим, как восстанавливается кластер
# Параллельно: write-loop в лидера (HAProxy :5400) и read-loop на реплики (HAProxy :5401).
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PYTHON:-python3}
ts() { $PY -c "import datetime;t=datetime.datetime.now(datetime.timezone.utc);print(t.strftime('%H:%M:%S.')+f'{t.microsecond//1000:03d}')"; }
api() { for p in 8008 8009 8010; do curl -s -m 2 "localhost:$p/$1" && return 0; done; return 1; }
show() { api cluster | $PY -c "import json,sys;print(', '.join(f\"{m['name']}={m['role']}/{m['state']}\" for m in json.load(sys.stdin)['members']))"; }
echo "кластер до: $(show)"
W=$(mktemp); R=$(mktemp)
$PY scripts/probe_write.py --seconds 150 --threads 2 --sleep-ms 5 --acks /dev/null > "$W" 2>&1 &
PW=$!
$PY scripts/probe_write.py --seconds 150 --threads 1 --sleep-ms 100 --read --dsn postgresql://postgres:postgres@localhost:5401/postgres > "$R" 2>&1 &
PR=$!
sleep 10; echo "$(ts) UTC: docker stop hw01-etcd3-1     (1 из 3 etcd остановлен)"; docker stop hw01-etcd3-1 >/dev/null
sleep 30; echo "$(ts) UTC: кластер при 2 живых etcd: $(show)"
echo "$(ts) UTC: docker stop hw01-etcd2-1     (2 из 3 etcd остановлены: кворума нет)"; docker stop hw01-etcd2-1 >/dev/null
sleep 40; echo "$(ts) UTC: кластер при 1 живом etcd: $(show 2>/dev/null || echo 'REST API Patroni не отвечает')"
echo "$(ts) UTC: docker start hw01-etcd2-1 hw01-etcd3-1   (кворум возвращён)"; docker start hw01-etcd2-1 hw01-etcd3-1 >/dev/null
wait $PW $PR || true
echo; echo "=== write-loop (лидер, HAProxy :5400) ==="; cat "$W"
echo; echo "=== read-loop (реплики, HAProxy :5401) ==="; cat "$R"
echo; echo "кластер после: $(show)"
rm -f "$W" "$R"
