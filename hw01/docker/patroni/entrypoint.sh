#!/bin/bash
# Рендерит patroni.yml из шаблона и запускает Patroni (он сам стартует и обслуживает PostgreSQL).
set -euo pipefail

: "${NODE_NAME:?NODE_NAME обязателен}"
: "${PATRONI_SCOPE:=hw01-cluster}"
export NODE_NAME PATRONI_SCOPE

DATA_DIR=/var/lib/postgresql/data
mkdir -p "$DATA_DIR"
chmod 700 "$DATA_DIR"

envsubst '${NODE_NAME} ${PATRONI_SCOPE}' < /etc/patroni.yml.tpl > /tmp/patroni.yml
exec /opt/patroni/bin/patroni /tmp/patroni.yml
