# Лог внешних источников

README задания требует указывать внешние источники кода и рецептов. Готовых решений (репозиториев однокурсников или других студентов) **не использовалось**.

## Использование LLM

Код, SQL-миграции, конфигурация стенда, генератор данных, тесты инвариантов и тексты документов подготовлены с помощью LLM — **Claude (Anthropic) через Claude Code**; README задания использование LLM разрешает.
Всё, что в документах названо «измерением», получено реальным запуском на стенде автора (команда и сырой вывод — в [`docs/failover_journal.md`](failover_journal.md) и [`docs/failover_raw/`](failover_raw/)); результат чекера — вывод `python hw01_check.py` на собранном с нуля стенде.

## Документация, по которой настраивались компоненты

| что | источник |
|-----|----------|
| `EXCLUDE USING gist`, `btree_gist`, `tstzrange` | https://www.postgresql.org/docs/18/sql-createtable.html#SQL-CREATETABLE-EXCLUDE ; https://www.postgresql.org/docs/18/btree-gist.html |
| `CONSTRAINT TRIGGER … DEFERRABLE INITIALLY DEFERRED` | https://www.postgresql.org/docs/18/sql-createtrigger.html |
| вычисляемые колонки (`GENERATED … STORED`) и значение по умолчанию `VIRTUAL` в PG18 | https://www.postgresql.org/docs/18/ddl-generated-columns.html |
| `COPY`, `pg_stat_replication`, `pg_replication_slots`, `pg_stat_wal_receiver`, публикации | https://www.postgresql.org/docs/18/ (разделы COPY, Monitoring, Logical Replication) |
| Patroni: конфигурация, `post_bootstrap`, `basebackup`, REST API, `patronictl`, синхронный режим | https://patroni.readthedocs.io/en/latest/ |
| HAProxy для Patroni (`option httpchk`, проверки `/primary` и `/replica`) | https://patroni.readthedocs.io/en/latest/ (раздел HAProxy), https://docs.haproxy.org/ |
| PgBouncer: режимы пула, `auth_type`, `ignore_startup_parameters` | https://www.pgbouncer.org/config.html |
| etcd: кластер из трёх узлов (флаги `--initial-cluster`) | https://etcd.io/docs/v3.5/op-guide/clustering/ |
| диаграммы | https://mermaid.js.org/syntax/entityRelationshipDiagram.html |

## Образы и пакеты

`postgres:18-bookworm`, `quay.io/coreos/etcd:v3.5.21`, `haproxy:3.0-alpine`, `debian:bookworm-slim` (+ пакет `pgbouncer`), Python-пакеты `patroni[etcd3]==3.0.0`, `ydiff==1.2`, `psycopg2-binary`, `PyYAML`.
Версия Patroni закреплена осознанно — см. [ADR-0007](adr/0007-patroni-version.md).

## Код, взятый из внешних источников

Нет. Скрипты и конфигурация написаны для этого стенда; конфигурация Patroni/HAProxy/PgBouncer собрана по документации выше, а не скопирована из чужих репозиториев.
