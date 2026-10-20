# Журнал архитектурных решений (ADR)

Формат каждой записи: **контекст → рассмотренные варианты → выбор → цена выбора**. В каждой записи есть **отвергнутая альтернатива с причиной отказа**.
Ссылки на измерения ведут в [`docs/failover_journal.md`](../failover_journal.md) и в чекер ([`hw01_check.py`](../../hw01_check.py)).

| № | решение | статус |
|---|---------|--------|
| [0001](0001-business-key-format.md) | Формат business key: префикс + номер, а не `serial`/UUID | принято |
| [0002](0002-price-history-scd2.md) | История цен: SCD2-интервалы с `EXCLUDE` | принято |
| [0003](0003-stock-materialization.md) | Остаток: материализованная таблица + триггер, а не расчёт из журнала | принято |
| [0004](0004-rx-invariant-across-services.md) | Инвариант I10 (рецептурность живёт в каталоге, ограничение нужно в POS): локальная копия + snapshot + отложенный триггер | принято |
| [0005](0005-attribute-history-via-trigger.md) | История атрибутов SKU/клиента: таблица `*_history` + триггер, а не SCD2 в основной таблице | принято |
| [0006](0006-four-databases-one-cluster.md) | Четыре БД в одном кластере Patroni (не схемы и не четыре кластера) | принято |
| [0007](0007-patroni-version.md) | Закрепление Patroni 3.0.0 из-за состояния реплик в `/cluster` | принято (с оговоркой) |
| [0008](0008-pgbouncer-transaction-pooling.md) | PgBouncer в режиме transaction pooling | принято |
| [0009](0009-generated-columns-for-amounts.md) | Суммы позиций — `GENERATED ALWAYS … STORED`, а не CHECK-равенство | принято |
