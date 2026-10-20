# Журнал отказоустойчивости — ДЗ №1

> Заполнено **своими** измерениями на **своём** стенде (`docker compose up -d`). Все эксперименты выполнены один раз подряд,
> в порядке нумерации, на одном и том же поднятом кластере. Сырые выводы и логи лежат в [`docs/failover_raw/`](failover_raw/);
> здесь приведены выдержки (полный лог write-loop первого эксперимента — 8.5 МБ — в репозиторий не добавлен).
> Инструменты: [`scripts/probe_write.py`](../scripts/probe_write.py) (write/read-loop), [`scripts/failover_loss_demo.sh`](../scripts/failover_loss_demo.sh),
> [`scripts/etcd_failure_demo.sh`](../scripts/etcd_failure_demo.sh), [`scripts/connection_storm.py`](../scripts/connection_storm.py),
> [`scripts/network_partition_demo.sh`](../scripts/network_partition_demo.sh).

## Как устроен измеритель

`probe_write.py` — N потоков в цикле делают `INSERT … RETURNING id` + `COMMIT` в таблицу `probe_writes` (база `postgres`, через HAProxy `:5400` — прямо на лидера,
без пула, чтобы измерять именно Patroni/HAProxy). Транзакция считается **подтверждённой** только после успешного `COMMIT`. При ошибке поток закрывает соединение и пробует снова (клиент
без знания о смене лидера). Метки времени — UTC с точностью до миллисекунд, их можно сопоставлять с логами Patroni.
**RTO** = максимальный промежуток между двумя соседними успешными записями (последний успех до провала → первый успех после).
Проверка потерь: после эксперимента все подтверждённые клиенту `id` ищутся в таблице на **новом** лидере; недостающие = потерянные подтверждённые транзакции.

## 0. Стенд

| параметр | значение |
|----------|----------|
| машина (CPU / RAM / диск) | Apple M4 Pro, 12 ядер, 24 ГБ RAM (SSD); Docker Desktop VM: 12 vCPU, ~8 ГБ RAM |
| ОС | macOS 26.5.1 (Darwin 25.5.0), контейнеры Debian 12 (bookworm), aarch64 |
| Docker / docker compose | Docker 29.5.3 / Compose v5.1.4 |
| образ PostgreSQL | `postgres:18-bookworm` → PostgreSQL 18.6; Patroni 3.0.0 (версия закреплена, см. [ADR-0007](adr/0007-patroni-version.md)) |
| топология | 3 узла PostgreSQL под Patroni (`pg1`, `pg2`, `pg3`) + etcd из 3 узлов (`etcd1..3`) + HAProxy (`:5400` лидер, `:5401` реплики) + 2 PgBouncer (`:6432` → лидер, `:6433` → реплики); REST API Patroni на `:8008/:8009/:8010` |
| `ttl` / `loop_wait` / `retry_timeout` Patroni | 30 / 10 / 10 с (`maximum_lag_on_failover` = 1 МБ) |
| `synchronous_mode` | `false` (Э1–Э4, Э6–Э8), `true` в Э5 (включается и выключается в ходе эксперимента) |

Схема стенда:

```
                      клиенты (приложение, чекер, генератор)
                           │                         │
                  :6432 PgBouncer-rw         :6433 PgBouncer-ro        (pool_mode = transaction)
                           │                         │
                           └──────────┬──────────────┘
                                  HAProxy  :5000 (внешний :5400) → /primary  → лидер
                                           :5001 (внешний :5401) → /replica  → реплики (round-robin)
                         ┌────────────┼────────────┐
                        pg1          pg2          pg3        ← Patroni + PostgreSQL 18, потоковая репликация
                         └────────────┼────────────┘
                              ┌───────┼───────┐
                            etcd1   etcd2   etcd3            ← DCS, кворум 2 из 3
```

Снимок стенда перед экспериментами (`docs/failover_raw/00_stand.txt`):

```
Apple M4 Pro
ncpu=12 ram_bytes=25769803776
docker VM: cpus=12 mem=8321232896 server=29.5.3
PostgreSQL 18.6 (Debian 18.6-1.pgdg12+2) on aarch64-unknown-linux-gnu, compiled by gcc (Debian 12.2.0-14+deb12u1) 12.2.0, 64-bit
patroni 3.0.0
    "ttl": 30,
    "loop_wait": 10,
    "retry_timeout": 10,
    "synchronous_mode": false,
            "name": "pg1",
            "role": "leader",
            "state": "running",
            "name": "pg2",
            "role": "replica",
            "state": "running",
            "lag": 0
            "name": "pg3",
            "role": "replica",
            "state": "running",
            "lag": 0
```

---

## Э1. Baseline: запись под нагрузкой

Что делаем: пишем в базу в цикле, фиксируем нормальное поведение (асинхронная репликация, лидер `pg1`).

```bash
python scripts/probe_write.py --seconds 60 --threads 4 --log e1.log --acks e1.acks
python scripts/probe_write.py --verify e1.acks
```

```
# Э1 baseline async, 2026-10-20T20:29:19Z
20:29:20.044 UTC: старт, 4 потоков, 60 с, dsn=localhost:5400/postgres

итог: длительность 60.0 с, успешных транзакций 211487, ошибок 0
      TPS (успешных/с) = 3524.7;  задержка записи: медиана 1.11 мс, p95 1.39 мс
      провалов записи дольше 1 с нет (макс. промежуток между успехами 18 мс)
      подтверждённые id сохранены: /tmp/e1.acks (211487)
проверка на узле 172.19.0.5/32 (in_recovery=False): подтверждено клиенту 211487, найдено в таблице 211487, ПОТЕРЯНО подтверждённых транзакций: 0

(полный лог 8.5 МБ не хранится в репозитории; первые/последние строки)
20:29:20.060 w2 OK  lat_ms=14.7 id=1
20:29:20.060 w1 OK  lat_ms=15.1 id=2
...
20:30:20.045 w2 OK  lat_ms=1.1 id=211485
20:30:20.045 w1 OK  lat_ms=1.0 id=211487
```

| метрика | значение |
|---------|----------|
| успешных транзакций за 60 с | 211 487 (ошибок 0) |
| TPS | 3 525 |
| задержка записи, медиана / p95 | 1.11 мс / 1.39 мс |

Вывод: один лидер без отказов — 3.5 тыс. подтверждённых транзакций в секунду, ни одной ошибки; это эталон для остальных экспериментов.


---

## Э2. Аварийная потеря лидера

Что делаем: `docker kill` контейнера-лидера при работающем write-loop (асинхронная репликация).

```bash
python scripts/probe_write.py --seconds 60 --threads 4 --acks e2.acks &
sleep 15 && docker kill hw01-pg1-1          # убиваем лидера pg1 (SIGKILL)
python scripts/probe_write.py --verify e2.acks
```

Write-loop вокруг провала (последняя успешная запись, ошибки, первая успешная после восстановления):

```
20:31:06.447 w3 OK  lat_ms=1.5 id=264084
20:31:06.447 w4 OK  lat_ms=1.1 id=264085
20:31:06.448 w1 OK  lat_ms=1.4 id=264086
20:31:06.448 w3 ERR lat_ms=1.4 server closed the connection unexpectedly
20:31:06.448 w4 ERR lat_ms=1.4 server closed the connection unexpectedly
20:31:06.449 w2 ERR lat_ms=2.6 server closed the connection unexpectedly
20:31:06.449 w1 ERR lat_ms=1.5 server closed the connection unexpectedly
20:31:31.527 w4 ERR lat_ms=6.3 connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
20:31:31.588 w2 ERR lat_ms=5.8 connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
20:31:31.588 w4 ERR lat_ms=5.7 connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
20:31:31.588 w1 ERR lat_ms=6.5 connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
20:31:31.588 w3 ERR lat_ms=6.4 connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
20:31:31.666 w3 OK  lat_ms=25.4 id=264102
20:31:31.667 w4 OK  lat_ms=26.1 id=264101
20:31:31.667 w2 OK  lat_ms=25.7 id=264103
```

Сводка write-loop и проверка потерь:

```
до: pg1=leader, pg2=replica, pg3=replica
20:31:06.428 UTC: команда: docker kill hw01-pg1-1   (убиваем лидера pg1)
итог: длительность 60.0 с, успешных транзакций 136815, ошибок 876
      TPS (успешных/с) = 2280.2;  задержка записи: медиана 0.98 мс, p95 1.31 мс
      окно недоступности записи (RTO): 25.22 с  (последняя успешная запись 20:31:06.448, первая успешная после 20:31:31.666 UTC)
      отклонено попыток в окне: 876
        x4     20:31:06.448 .. 20:31:06.449  server closed the connection unexpectedly
        x12    20:31:10.509 .. 20:31:18.630  connection to server at "localhost" (::1), port 5400 failed: timeout expired
        x860   20:31:18.691 .. 20:31:31.588  connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
после: pg2=leader/running, pg3=replica/running
проверка на узле 172.19.0.6/32 (in_recovery=False): подтверждено клиенту 136815, найдено в таблице 136815, ПОТЕРЯНО подтверждённых транзакций: 0
```

Лог Patroni нового лидера `pg2` с таймстемпами (потеря лидерского ключа → promote):

```
pg2: 2026-10-20 20:31:06.447 UTC [54] FATAL:  could not receive data from WAL stream: server closed the connection unexpectedly
pg2: 2026-10-20 20:31:06.447 UTC [29] LOG:  invalid record length at 0/13DE41F8: expected at least 24, got 0
pg2: 2026-10-20 20:31:11.849 UTC [660] FATAL:  streaming replication receiver "hw01-cluster" could not connect to the primary server: connection to server at "pg1" (198.18.3.149), port 5432 failed: server close
pg2: 2026-10-20 20:31:11.849 UTC [29] LOG:  waiting for WAL to become available at 0/13DE4210
pg2: 2026-10-20 20:31:28,175 INFO: Got response from pg3 http://pg3:8008/patroni: {"state": "running", "postmaster_start_time": "2026-10-20 20:26:53.475727+00:00", "role": "replica", "server_version": 180006, "
pg2: 2026-10-20 20:31:28,438 WARNING: Request failed to pg1: GET http://pg1:8008/patroni (HTTPConnectionPool(host='pg1', port=8008): Max retries exceeded with url: /patroni (Caused by ProtocolError('Connection 
pg2: 2026-10-20 20:31:28,534 WARNING: Could not activate Linux watchdog device: "Can't open watchdog device: [Errno 2] No such file or directory: '/dev/watchdog'"
pg2: 2026-10-20 20:31:28.552 UTC [710] ERROR:  replication slot "pg3" does not exist
pg2: 2026-10-20 20:31:28.552 UTC [710] STATEMENT:  START_REPLICATION SLOT "pg3" 0/13000000 TIMELINE 1
pg2: 2026-10-20 20:31:28.580 UTC [29] LOG:  received promote request
pg2: 2026-10-20 20:31:28.580 UTC [29] LOG:  redo done at 0/13DE41C8 system usage: CPU: user: 2.11 s, system: 1.79 s, elapsed: 274.98 s
pg2: 2026-10-20 20:31:28.580 UTC [29] LOG:  last completed transaction was at log time 2026-10-20 20:31:06.446091+00
pg2: 2026-10-20 20:31:28,579 INFO: promoted self to leader by acquiring session lock
pg2: 2026-10-20 20:31:28,581 INFO: cleared rewind state after becoming the leader
pg2: 2026-10-20 20:31:28.585 UTC [29] LOG:  selected new timeline ID: 2
pg2: 2026-10-20 20:31:28.603 UTC [29] LOG:  archive recovery complete
pg2: 2026-10-20 20:31:28.619 UTC [27] LOG:  checkpoint starting: force
pg2: 2026-10-20 20:31:28.621 UTC [22] LOG:  database system is ready to accept connections
pg2: 2026-10-20 20:31:28,580 INFO: Lock owner: pg2; I am pg2
pg2: 2026-10-20 20:31:28,665 INFO: updated leader lock during promote
```

| метрика | значение |
|---------|----------|
| последняя успешная запись (время) | 20:31:06.448 UTC (убийство: 20:31:06.428) |
| первая успешная запись после failover (время) | 20:31:31.666 UTC |
| **RTO (окно недоступности записи), с** | **25.22** |
| число отклонённых транзакций | 876 попыток (4 «server closed», 12 «timeout expired», 860 «connection closed» от HAProxy без живого бэкенда) |
| кто стал новым лидером | `pg2` (timeline 1 → 2) |
| потеряны ли подтверждённые транзакции (да/нет, как проверили) | нет: подтверждено 136 815, найдено на новом лидере 136 815 (`--verify`) |

Почему RTO получился именно такой (сопоставьте с `ttl` и `loop_wait`): живой лидер обновляет ключ в etcd раз в `loop_wait` = 10 с, а ключ живёт `ttl` = 30 с.
После `SIGKILL` никто не снимает ключ — реплики **обязаны ждать истечения ttl**. Promote в логе — в 20:31:28.579, значит ключ истёк в ~20:31:28.5, т.е. последнее обновление было ≈ 20:30:58.5,
за ~8 с до удара: оставшийся срок ключа ≈ 22 с. К ним добавляется ~3.1 с, пока HAProxy замечает нового лидера (проверка `/primary` раз в 2 с, `fall 3`/`rise 2`): первая успешная запись в 20:31:31.666 =
28.579 + 3.09. Итого 22.0 + 3.1 ≈ 25.2 с. Теоретический диапазон RTO для этой конфигурации — от ≈ `ttl − loop_wait` + HAProxy (≈ 23 с) до ≈ `ttl` + HAProxy (≈ 33 с) в зависимости от того,
в какой фазе цикла пришёл отказ; в четырёх аварийных замерах этого журнала получилось 25.2 / 29.9 / 32.7 / 33.1 с. Клиенту без повторов это окно — просто отказ записи; с повторами — задержка.


---

## Э3. Возврат упавшего узла

Что делаем: поднимаем убитый узел обратно.

```bash
docker start hw01-pg1-1        # pg1 — бывший лидер, убитый в Э2; текущий лидер — pg2
```

Лог Patroni на `pg1` после старта (ключевые строки):

```
20:32:31.103 UTC: команда: docker start hw01-pg1-1
2026-10-20 20:32:32,109 INFO: Local timeline=1 lsn=0/14000028
2026-10-20 20:32:32,116 INFO: primary_timeline=2
2026-10-20 20:32:32,119 INFO: running pg_rewind from pg2
2026-10-20 20:32:32,131 INFO: running pg_rewind from dbname=postgres user=postgres host=pg2 port=5432 target_session_attrs=read-write
2026-10-20 20:32:33,137 INFO: pg_rewind exit code=0
2026-10-20 20:32:33,138 INFO:  stderr=pg_rewind: servers diverged at WAL location 0/13DE41F8 on timeline 1
pg_rewind: rewinding from last common checkpoint at 0/4000130 on timeline 1
pg_rewind: Done!
2026-10-20 20:32:33,139 INFO: starting as a secondary
2026-10-20 20:32:33.275 UTC [50] LOG:  redo starts at 0/40000D8
2026-10-20 20:32:33.821 UTC [50] LOG:  consistent recovery state reached at 0/15DF2470
2026-10-20 20:32:34,275 INFO: no action. I am (pg1), a secondary, and following a leader (pg2)
2026-10-20 20:32:43.846 UTC [85] LOG:  started streaming WAL from primary at 0/15000000 on timeline 2
```

Состояние `pg1` во времени (опрос REST API раз в секунду) и репликация на лидере:

```
LSN лидера pg2 перед возвратом pg1: 0/15DF2470
20:32:34.351 UTC: pg1 снова в кластере: replica running 0, прошло 3 с
 c: pg1 -> нет данных
 c: pg1 -> replica stopped unknown
 c: pg1 -> replica running 0
pg3|streaming|async|0
LSN лидера сейчас: 0/15DF2470|объём WAL между точками, байт: 0
pg1 -> 'streaming' (этот запрос выполнен уже после старта репликации). Точное время старта — по логу pg1: 20:32:43.846 UTC,
 application_name |   state   | sync_state | lag_bytes 
 pg1              | streaming | async      |         0
 pg3              | streaming | async      |         0
```

| метрика | значение |
|---------|----------|
| понадобился ли `pg_rewind` | да (`pg_rewind exit code=0`, ≈ 1 с): «servers diverged at WAL location 0/13DE41F8 on timeline 1» |
| время до состояния `streaming`, с | 3.2 с до `replica / running / lag 0` в REST API Patroni; 12.7 с до фактического `started streaming WAL` (20:32:43.846): слот репликации `pg1` Patroni создаёт на лидере на следующей итерации цикла (`loop_wait` = 10 с) |
| объём догнанного WAL | ≈ 32 МБ (от точки расхождения 0/13DE41F8 до LSN лидера 0/15DF2470 = 32.06 МБ; с локального LSN `pg1` на старте 0/14000028 — 29.95 МБ) |
| роль узла после возврата | `replica` (лидером остаётся `pg2`; Patroni **не** возвращает лидерство бывшему лидеру) |

Почему понадобился `pg_rewind`: `pg2` был повышен с конца **своего** WAL (`0/13DE41F8`, см. лог Э2: «invalid record length at 0/13DE41F8» — дальше WAL нет), а `pg1` к моменту `SIGKILL` успел записать WAL **дальше** этой точки (сколько именно — по логам не видно);
эти записи не дошли до реплик, и истории на timeline 1 разошлись (`servers diverged at WAL location 0/13DE41F8`). Просто подписаться на нового лидера нельзя (в локальном WAL есть то, чего нет у лидера), а `basebackup` на всю базу дороже;
`use_pg_rewind: true` + `wal_log_hints = on` позволяют перемотать только расхождение (≈ 1 с вместо копирования всего каталога).


---

## Э4. Плановое переключение

Что делаем: `switchover` через Patroni при работающем write-loop. Эксперимент пришлось провести дважды — первая попытка под полной нагрузкой была отклонена.

### Э4а. Под полной нагрузкой (3.5 тыс. TPS) — отказ

```bash
docker compose exec pg2 patronictl -c /tmp/patroni.yml switchover --force --leader pg2 --candidate pg1
```

```
20:34:29.020 UTC: команда: docker compose exec pg2 patronictl -c /tmp/patroni.yml switchover --force --leader pg2 --candidate pg1
Switchover failed, details: 412, switchover is not possible: no good candidates have been found
итог: длительность 40.0 с, успешных транзакций 142908, ошибок 0
      TPS (успешных/с) = 3572.6;  задержка записи: медиана 1.10 мс, p95 1.38 мс
      провалов записи дольше 1 с нет (макс. промежуток между успехами 6 мс)
проверка на узле 172.19.0.6/32 (in_recovery=False): подтверждено клиенту 142908, найдено в таблице 142908, ПОТЕРЯНО подтверждённых транзакций: 0
```

Patroni **отказал**: `switchover is not possible: no good candidates have been found`, хотя обе реплики здоровы и `lag = 0` в `patronictl list`.
Причина (проверено измерением, `docs/failover_raw/e4_wal_rate.txt`): Patroni сравнивает отставание кандидата с `maximum_lag_on_failover` = **1 МБ**, но данные о позиции реплики в DCS обновляются
раз в `loop_wait` = 10 с. При полной нагрузке за эти 10 с лидер пишет **6.93 МБ** WAL — кандидат формально «отстаёт» на несколько МБ.

```
полная нагрузка (sleep 0): TPS (успешных/с) = 3477.8;  задержка записи: медиана 1.11 мс, p95 1.46 мс | WAL за 10 с: 6.93 МБ (порог maximum_lag_on_failover = 1 МБ)
умеренная нагрузка (sleep 5 мс): TPS (успешных/с) = 548.8;  задержка записи: медиана 1.29 мс, p95 1.94 мс | WAL за 10 с: 1.10 МБ (порог maximum_lag_on_failover = 1 МБ)
```

### Э4б. При умеренной нагрузке (~135 TPS, 4 потока с паузой 20 мс)

```bash
python scripts/probe_write.py --seconds 40 --threads 4 --sleep-ms 20 --acks e4b.acks &
sleep 12 && docker compose exec pg1 patronictl -c /tmp/patroni.yml switchover --force --leader pg1 --candidate pg2
```

```
до: pg1=leader, pg2=replica, pg3=replica
20:36:39.612 UTC: команда: docker compose exec pg1 patronictl -c /tmp/patroni.yml switchover --force --leader pg1 --candidate pg2
20:36:42.049 UTC: команда вернула управление
итог: длительность 40.0 с, успешных транзакций 5392, ошибок 32
      TPS (успешных/с) = 134.8;  задержка записи: медиана 3.53 мс, p95 5.92 мс
      окно недоступности записи (RTO): 4.69 с  (последняя успешная запись 20:36:40.063, первая успешная после 20:36:44.750 UTC)
      отклонено попыток в окне: 30
20:36:40.062 w2 OK  lat_ms=1.7 id=674715
20:36:40.062 w3 OK  lat_ms=1.7 id=674716
20:36:40.063 w1 OK  lat_ms=2.1 id=674717
20:36:40.084 w4 ERR lat_ms=0.4 server closed the connection unexpectedly
20:36:40.084 w3 ERR lat_ms=0.2 server closed the connection unexpectedly
20:36:40.084 w2 ERR lat_ms=0.1 server closed the connection unexpectedly
20:36:40.084 w1 ERR lat_ms=0.1 server closed the connection unexpectedly
20:36:44.161 w2 ERR lat_ms=4005.8 connection to server at "localhost" (::1), port 5400 failed: timeout expired
20:36:44.749 w1 ERR lat_ms=17.6 cannot execute INSERT in a read-only transaction
20:36:44.749 w3 ERR lat_ms=17.2 cannot execute INSERT in a read-only transaction
20:36:44.750 w4 OK  lat_ms=26.2 id=674719
20:36:44.751 w2 OK  lat_ms=20.7 id=674720
20:36:44.776 w2 OK  lat_ms=2.4 id=674721
        x4     20:36:40.084 .. 20:36:40.084  server closed the connection unexpectedly
        x4     20:36:44.161 .. 20:36:44.166  connection to server at "localhost" (::1), port 5400 failed: timeout expired
        x24    20:36:44.257 .. 20:36:44.928  cannot execute INSERT in a read-only transaction
после: pg1=replica/running, pg2=leader/running, pg3=replica/running
проверка на узле 172.19.0.6/32 (in_recovery=False): подтверждено клиенту 5392, найдено в таблице 5392, ПОТЕРЯНО подтверждённых транзакций: 0
```

| метрика | значение |
|---------|----------|
| RTO планового переключения, с | **4.69** (последняя запись 20:36:40.063 → первая после 20:36:44.750); сама команда `switchover` вернула управление через 2.4 с |
| число отклонённых транзакций | 32 за весь прогон: 4 «server closed», 4 «timeout expired», 24 «read-only transaction» (30 из них — внутри окна, ещё 2 — сразу после первой успешной записи, до 20:36:44.928, пока HAProxy ещё отдавал часть соединений старому лидеру); подтверждённых потерь 0 (5 392 из 5 392) |

Во сколько раз плановое переключение дешевле аварийного и почему: ≈ **в 5.4 раза** (25.22 с → 4.69 с). При плановом переключении лидер сам, штатно и быстро останавливается (checkpoint, отдаёт ключ в etcd) —
поэтому **не нужно ждать истечения `ttl`**: главная составляющая аварийного RTO (≈ 22 с из 25) исчезает. Остаётся перезапуск/promote и обнаружение нового лидера HAProxy (≈ 3–4 с).
Вторая ценная разница — ни одна подтверждённая транзакция не теряется: лидер перед остановкой дожидается, пока кандидат догонит WAL.


---

## Э5. Цена синхронной репликации

Что делаем: включаем `synchronous_mode`, повторяем Э1 и Э2.

```bash
docker compose exec pg2 patronictl -c /tmp/patroni.yml edit-config --force -s synchronous_mode=true
```

```
команда: docker compose exec pg2 patronictl -c /tmp/patroni.yml edit-config --force -s synchronous_mode=true
-synchronous_mode: false
+synchronous_mode: true
| pg1    | pg1  | Replica      | running |  4 |         0 |
| pg2    | pg2  | Leader       | running |  4 |           |
| pg3    | pg3  | Sync Standby | running |  4 |         0 |
pg3
pg1|streaming|async
pg3|streaming|sync
--- Э1 повтор в sync-режиме (60 c, 4 потока):
итог: длительность 60.0 с, успешных транзакций 176977, ошибок 0
      TPS (успешных/с) = 2949.6;  задержка записи: медиана 1.33 мс, p95 1.58 мс
проверка на узле 172.19.0.6/32 (in_recovery=False): подтверждено клиенту 176977, найдено в таблице 176977, ПОТЕРЯНО подтверждённых транзакций: 0
```

Повтор Э2 (авария лидера) в синхронном режиме, `docker kill hw01-pg2-1`:

```
до: pg1=replica, pg2=leader, pg3=sync_standby
20:39:28.943 UTC: команда: docker kill hw01-pg2-1   (убиваем лидера pg2, synchronous_mode=true)
итог: длительность 70.0 с, успешных транзакций 138143, ошибок 1260
      TPS (успешных/с) = 1973.4;  задержка записи: медиана 1.03 мс, p95 1.46 мс
      окно недоступности записи (RTO): 32.73 с  (последняя успешная запись 20:39:26.981, первая успешная после 20:39:59.706 UTC)
      отклонено попыток в окне: 1260
после: pg1=sync_standby/running, pg3=leader/running
проверка на узле 172.19.0.8/32 (in_recovery=False): подтверждено клиенту 138143, найдено в таблице 138143, ПОТЕРЯНО подтверждённых транзакций: 0
```

**Самое важное — потеря подтверждённых транзакций.** В Э2 потерь не было (0) только потому, что репликация отставала на доли миллисекунды; чтобы проверить гарантию, а не удачу,
сценарий усилен ([`scripts/failover_loss_demo.sh`](../scripts/failover_loss_demo.sh)): на 8-й секунде обе реплики на 0.5 с отключаются от сети, пока лидер продолжает работать, затем лидер убивается.

Асинхронный режим (`synchronous_mode=false`), лидер `pg1`:

```
synchronous_mode=False; лидер=pg1; реплики=pg2 pg3; пауза между отключением реплик и убийством лидера = 0.5 с
20:43:22.057 UTC: docker network disconnect hw01_default hw01-pg2-1
20:43:22.240 UTC: docker network disconnect hw01_default hw01-pg3-1
20:43:22.923 UTC: docker kill hw01-pg1-1   (лидер убит, реплики всё ещё отрезаны)
20:43:23.101 UTC: docker network connect hw01_default hw01-pg2-1
20:43:23.145 UTC: docker network connect hw01_default hw01-pg3-1
...
итог: длительность 70.0 с, успешных транзакций 146981, ошибок 2100
TPS (успешных/с) = 2099.7;  задержка записи: медиана 0.98 мс, p95 1.26 мс
окно недоступности записи (RTO): 33.11 с  (последняя успешная запись 20:43:22.941, первая успешная после 20:43:56.053 UTC)
20:43:22.941 w2 OK  lat_ms=1.6 id=1175668
20:43:56.054 w1 OK  lat_ms=17.9 id=1172953
x220   20:43:24.023 .. 20:43:27.903  cannot execute INSERT in a read-only transaction
проверка на узле 172.19.0.6/32 (in_recovery=False): подтверждено клиенту 146981, найдено в таблице 144233, ПОТЕРЯНО подтверждённых транзакций: 2748
```

Синхронный режим (`synchronous_mode=true`), лидер `pg3`:

```
synchronous_mode=True; лидер=pg3; реплики=pg1 pg2; пауза между отключением реплик и убийством лидера = 0.5 с
20:41:23.740 UTC: docker network disconnect hw01_default hw01-pg1-1
20:41:23.905 UTC: docker network disconnect hw01_default hw01-pg2-1
20:41:24.597 UTC: docker kill hw01-pg3-1   (лидер убит, реплики всё ещё отрезаны)
20:41:24.818 UTC: docker network connect hw01_default hw01-pg1-1
20:41:24.863 UTC: docker network connect hw01_default hw01-pg2-1
...
итог: длительность 70.0 с, успешных транзакций 149992, ошибок 1148
TPS (успешных/с) = 2142.7;  задержка записи: медиана 1.06 мс, p95 1.41 мс
окно недоступности записи (RTO): 29.95 с  (последняя успешная запись 20:41:23.843, первая успешная после 20:41:53.795 UTC)
20:41:23.843 w2 OK  lat_ms=1.7 id=1017359
20:41:24.632 w2 ERR lat_ms=789.2 server closed the connection unexpectedly
проверка на узле 172.19.0.5/32 (in_recovery=False): подтверждено клиенту 149992, найдено в таблице 149992, ПОТЕРЯНО подтверждённых транзакций: 0
```

| метрика | async | sync | дельта |
|---------|-------|------|--------|
| TPS | 3 525 | 2 950 | −575 (**−16.3 %**) |
| задержка записи p95 | 1.39 мс | 1.58 мс | +0.19 мс (медиана 1.11 → 1.33 мс, +0.22 мс) |
| RTO при аварии лидера | 25.22 с (Э2); 33.11 с (опыт с отключением реплик) | 32.73 с; 29.95 с (опыт с отключением реплик) | различие не от режима: разброс 25–33 с задаёт фаза цикла `ttl` (см. Э2) |
| потеря подтверждённых транзакций | **2 748** (опыт с отключением реплик на 0.5 с; 0 в «мирном» Э2) | **0** | −2 748 |

Вывод: за что именно мы заплатили и в каком размере. Синхронная репликация стоит **~16 % пропускной способности** и **~0.2 мс** к каждой записи (лидер ждёт подтверждения от синхронной реплики перед `COMMIT`) и — главное —
**доступностью записи при проблемах реплик**: в опыте с sync лидер 0.79 с вообще не подтверждал коммиты, пока реплики были отрезаны (ошибки с `lat_ms=789` в логе). Взамен при падении лидера подтверждённые транзакции не теряются:
в async-опыте клиенту было подтверждено 146 981 транзакций, на новом лидере их 144 233 — потеряно 2 748 (`id` на новом лидере «откатился» с 1 175 668 до 1 172 953). Для чеков и оплат в этом ДЗ потеря
подтверждённых данных недопустима, поэтому рекомендуемая конфигурация — sync (см. выводы в конце). RTO режим не меняет: его определяет `ttl`.

После эксперимента режим возвращён: `patronictl edit-config --force -s synchronous_mode=false`.


---

## Э6. Отказ DCS (etcd)

Что делаем: гасим сначала один узел etcd из трёх, затем второй ([`scripts/etcd_failure_demo.sh`](../scripts/etcd_failure_demo.sh)). Параллельно — write-loop в лидера (`:5400`) и read-loop на реплики (`:5401`).
Замечание честности: к этому моменту `pg1` был остановлен после Э5, поэтому эксперимент шёл на двух узлах PostgreSQL (`pg3` — лидер, `pg2` — реплика); для поведения DCS это не важно, `pg1` возвращён сразу после.

```bash
docker stop hw01-etcd3-1        # t = 10 c: 1 из 3 etcd остановлен
docker stop hw01-etcd2-1        # t = 40 c: 2 из 3 — кворума нет
docker start hw01-etcd2-1 hw01-etcd3-1   # t = 80 c: кворум возвращён
```

Лог Patroni лидера `pg3`: что он начал делать, потеряв DCS:

```
20:45:11.850 UTC: docker stop hw01-etcd3-1     (1 из 3 etcd остановлен)
20:45:43.026 UTC: кластер при 2 живых etcd: pg2=replica/running, pg3=leader/running
20:45:43.073 UTC: docker stop hw01-etcd2-1     (2 из 3 etcd остановлены: кворума нет)
20:46:24.236 UTC: кластер при 1 живом etcd: pg2=replica/running, pg3=leader/running
20:46:24.279 UTC: docker start hw01-etcd2-1 hw01-etcd3-1   (кворум возвращён)
--- Patroni, лидер pg3:
2026-10-20 20:45:54,173 ERROR: Error communicating with DCS
2026-10-20 20:45:54,173 INFO: demoting self because DCS is not accessible and I was a leader
2026-10-20 20:45:54.318 UTC [570] LOG:  database system is ready to accept read-only connections
2026-10-20 20:45:55.321 UTC [584] ERROR:  cannot execute INSERT in a read-only transaction
```

Write-loop и read-loop:

```
=== write-loop (лидер, HAProxy :5400) ===
итог: длительность 150.0 с, успешных транзакций 29410, ошибок 1022
TPS (успешных/с) = 196.1;  задержка записи: медиана 1.71 мс, p95 3.37 мс
окно недоступности записи (RTO): 34.32 с  (последняя успешная запись 20:45:54.175, первая успешная после 20:46:28.491 UTC)
x2     20:45:54.183 .. 20:45:54.183  server closed the connection unexpectedly
x2     20:45:54.246 .. 20:45:54.246  connection to server at "localhost" (::1), port 5400 failed: FATAL:  the database system is shutting down
x82    20:45:55.322 .. 20:45:58.328  cannot execute INSERT in a read-only transaction
x936   20:45:58.387 .. 20:46:28.411  connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
=== read-loop (реплики, HAProxy :5401) ===
итог: длительность 150.1 с, успешных транзакций 1433, ошибок 0
TPS (успешных/с) = 9.5;  задержка записи: медиана 1.66 мс, p95 3.78 мс
провалов записи дольше 1 с нет (макс. промежуток между успехами 115 мс)
```

| метрика | значение |
|---------|----------|
| через сколько секунд кластер перешёл в read-only | **11.1 с** после потери кворума (2-й etcd остановлен в 20:45:43.073 → «demoting self because DCS is not accessible and I was a leader» в 20:45:54,173; первая ошибка записи 20:45:54.183). 1 остановленный etcd кластер **не заметил**: ни ошибок, ни смены лидера |
| остались ли доступны чтения | **да**: read-loop на репликах (`:5401`) 150 с, 1 433 запроса, 0 ошибок; лидер после demote тоже стал read-only (`cannot execute INSERT in a read-only transaction`) |
| что произошло при возврате etcd | в 20:46:24.279 etcd запущены; запись восстановилась в 20:46:28.491 (через ≈ 4.2 с); кластер после возврата: `pg2=replica`, `pg3=leader` — тот же лидер, смены ролей не произошло |
| сколько узлов etcd нужно, чтобы выжить при падении одного | **3** (кворум = 2 из 3: переживает падение одного, падение двух — нет: кластер уходит в read-only). Один узел etcd — единая точка отказа |

Объяснение: Patroni лидер обязан обновлять ключ в DCS раз в `loop_wait`; если DCS недоступен в течение `retry_timeout` = 10 с, лидер **сам** демоутится в read-only — иначе после возврата DCS мог бы оказаться второй лидер.
Цена безопасности: потеря кворума etcd = недоступность записи даже при полностью живых узлах PostgreSQL; чтение с реплик продолжает работать.


---

## Э7. Пул соединений под наплывом

Что делаем: `max_connections` (100) ломается наплывом клиентов без пула и держится с пулом. N клиентов одновременно открывают соединение и держат `pg_sleep(1)`
([`scripts/connection_storm.py`](../scripts/connection_storm.py)).

```bash
python scripts/connection_storm.py --dsn postgresql://app:app@localhost:5400/pos_service_db --ramp 50,80,85,90,95,100,150,300   # без пула
python scripts/connection_storm.py --dsn postgresql://app:app@localhost:6432/pos_service_db --ramp 50,100,300,600,900          # через PgBouncer
```

```
# Э7: наплыв клиентов, 2026-10-20T20:48:35Z

## без пула: напрямую в лидера (HAProxy :5400), PostgreSQL max_connections=100
100 3 
цель: localhost:5400/pos_service_db; каждый клиент держит pg_sleep(1.0) и закрывает соединение
 клиентов  успешно  ошибок  время установки соединения, мс (медиана / p95)  всего, с
       50       50       0                                   174.3 / 215.7       1.2
       80       80       0                                   364.6 / 375.0       1.4
       85       85       0                                   373.5 / 387.9       1.4
       90       90       0                                   102.7 / 236.1       1.2
       95       95       0                                   228.6 / 359.3       1.4
      100       96       4                                   392.8 / 417.0       1.4
          ошибка x4: connection to server at "localhost" (::1), port 5400 failed: FATAL:  remaining connection slots are reserved for roles with the SU
      150       96      54                                   365.6 / 382.6       1.4
          ошибка x36: connection to server at "localhost" (::1), port 5400 failed: FATAL:  remaining connection slots are reserved for roles with the SU
          ошибка x18: connection to server at "localhost" (::1), port 5400 failed: FATAL:  sorry, too many clients already
      300       96     204                                   321.8 / 360.7       1.6
          ошибка x119: connection to server at "localhost" (::1), port 5400 failed: FATAL:  remaining connection slots are reserved for roles with the SU
          ошибка x85: connection to server at "localhost" (::1), port 5400 failed: FATAL:  sorry, too many clients already

## с PgBouncer (:6432, pool_mode=transaction, default_pool_size=20, max_client_conn=1000)
цель: localhost:6432/pos_service_db; каждый клиент держит pg_sleep(1.0) и закрывает соединение
 клиентов  успешно  ошибок  время установки соединения, мс (медиана / p95)  всего, с
       50       50       0                                   148.8 / 150.1       3.3
      100      100       0                                   238.6 / 258.0       5.4
      300      300       0                                  380.1 / 1404.2      13.6
      600      600       0                                  739.0 / 1771.7      26.2
      900      900       0                                 1276.0 / 7358.5      38.7
```

Время установки одиночного соединения (100 последовательных `connect + SELECT 1`):

```
напрямую (HAProxy :5400): 100 последовательных подключений (connect + SELECT 1): медиана 5.5 мс, p95 6.8 мс, макс 16.9 мс
через PgBouncer (:6432): 100 последовательных подключений (connect + SELECT 1): медиана 13.8 мс, p95 15.6 мс, макс 26.7 мс
```

| метрика | без PgBouncer | с PgBouncer |
|---------|---------------|-------------|
| число клиентов, при котором начались ошибки | **100** (на 95 — 0 ошибок; на 100 — 96 успешных и 4 отказа; дальше стабильно 96 успешных) | ошибок нет до **900** клиентов (проверено до 900; `max_client_conn` = 1000) |
| текст первой ошибки | `FATAL: remaining connection slots are reserved for roles with the SUPERUSER attribute` (на 150+ клиентах добавляется `FATAL: sorry, too many clients already`) | — |
| время установки соединения | 5.5 мс (медиана), 6.8 мс (p95) | 13.8 мс (медиана), 15.6 мс (p95) — пул **медленнее** на одиночном подключении |

Лимит 96, а не 100: `max_connections` = 100, из них `superuser_reserved_connections` = 3 и ещё одно соединение занято служебным (Patroni). С пулом 900 клиентов обслужены за 38.7 с — не мгновенно, потому что
в БД одновременно работают лишь `default_pool_size` = 20 соединений, остальные стоят в очереди пула (медиана установки соединения при 900 клиентах — 1.28 с, p95 — 7.36 с).
Пул не ускоряет, а **защищает** `max_connections` и превращает отказ в ожидание.

Что перестаёт работать в transaction pooling и как мы это обошли. Состояние сессии живёт на **серверном** соединении, а оно меняется между транзакциями ([`e7_pooling_caveats.txt`](failover_raw/e7_pooling_caveats.txt)):

```
--- напрямую (:5400)
  SET statement_timeout='777ms' -> следующий SHOW statement_timeout: 777ms
  временная таблица в следующей транзакции: видна
  pg_advisory_unlock(4242) (сессионная блокировка, взятая раньше): True
--- через PgBouncer (:6432)
  SET statement_timeout='777ms' -> следующий SHOW statement_timeout: 0
  временная таблица в следующей транзакции: ОШИБКА: relation "tmp_demo" does not exist
  pg_advisory_unlock(4242) (сессионная блокировка, взятая раньше): False
 Поток-«перехватчик» получил серверное соединение с «протёкшим» SET statement_timeout='777ms' от другого клиента, и его pg_sleep(3) был отменён.
```

- `SET` (без `LOCAL`) теряется — и, хуже, **«протекает» к чужому клиенту**: в эксперименте чужой `pg_sleep(3)` был отменён моим `statement_timeout`. Обход: `SET LOCAL` внутри транзакции либо параметры на уровне роли/базы/сервера. Часовой пояс, `datestyle` и т.п.
  выставлены в конфигурации сервера (`timezone: UTC`, `datestyle: ISO, YMD` в Patroni), поэтому сессионный `SET` чекера (`SET TimeZone = 'UTC'`) ни на что не влияет.
- временные таблицы, `LISTEN/NOTIFY`, сессионные advisory-блокировки (`pg_advisory_lock`; в опыте `unlock` вернул `false`, а блокировка осталась висеть на серверном соединении пула — пришлось перезапустить пул).
  Обход: `pg_advisory_xact_lock` вместо сессионных; временные таблицы — внутри одной транзакции.
- серверные prepared statements на уровне протокола (PgBouncer 1.17 их не поддерживает; драйвер `psycopg2` использует простой протокол и этим не затронут).
- **логическая репликация / CDC (ДЗ №2) через пул не работает** (протокол репликации): CDC-инструменты подключаются к лидеру напрямую через HAProxy `:5400` (или отдельному порту), а не через `:6432`.


---

## Э8. Сетевой раздел (необязательный)

Что делаем: `docker network disconnect` для лидера — узел жив, но изолирован ([`scripts/network_partition_demo.sh`](../scripts/network_partition_demo.sh)).
Каждые ~4 с с самого изолированного узла (`docker exec`) проверяем `pg_is_in_recovery()` и пробуем записать.

```bash
docker network disconnect hw01_default hw01-pg1-1      # pg1 — лидер
# ... наблюдение 54 с ...
docker network connect hw01_default hw01-pg1-1
```

```
кластер до: pg1=leader/running, pg2=replica/running, pg3=replica/running; изолируем лидера pg1
20:53:18.337 UTC: docker network disconnect hw01_default hw01-pg1-1
20:53:22.726 UTC: изолированный pg1: in_recovery=false, default_transaction_read_only=off | попытка записи: ЗАПИСЬ ПРИНЯТА изолированным узлом, id=1322142
20:53:22.747 UTC:   вид кластера снаружи: pg1=leader/running, pg2=replica/running, pg3=replica/running
20:53:28.974 UTC: изолированный pg1: psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  the database system is shutting down | попытка записи: psql: error: connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" failed: FATAL:  the database system is shutting down
20:53:28.993 UTC:   вид кластера снаружи: pg1=leader/running, pg2=replica/running, pg3=replica/running
20:53:35.271 UTC:   вид кластера снаружи: pg1=leader/running, pg2=replica/running, pg3=replica/running
20:53:41.514 UTC:   вид кластера снаружи: pg2=leader/running, pg3=replica/running
20:53:47.753 UTC:   вид кластера снаружи: pg2=leader/running, pg3=replica/running
```

Вид write-loop снаружи и итог:

```
итог: длительность 75.0 с, успешных транзакций 12945, ошибок 404
TPS (успешных/с) = 172.6;  задержка записи: медиана 1.75 мс, p95 3.51 мс
окно недоступности записи (RTO): 23.38 с  (последняя успешная запись 20:53:18.430, первая успешная после 20:53:41.813 UTC)
20:53:18.430 w1 OK  lat_ms=1.2 id=1322141
20:53:18.430 w2 OK  lat_ms=1.5 id=1322140
20:53:41.813 w1 OK  lat_ms=20.1 id=1322148
20:53:41.813 w2 OK  lat_ms=24.0 id=1322149
x2     20:53:28.727 .. 20:53:28.727  server closed the connection unexpectedly
x402   20:53:28.793 .. 20:53:41.731  connection to server at "localhost" (::1), port 5400 failed: server closed the connection unexpectedly
проверка на узле 172.19.0.5/32 (in_recovery=False): подтверждено клиенту 12945, найдено в таблице 12945, ПОТЕРЯНО подтверждённых транзакций: 0
кластер после возврата: pg1=replica/running, pg2=leader/running, pg3=replica/running
```

| метрика | значение |
|---------|----------|
| что сделал изолированный узел | до ≈ 20:53:28.7 (≈ 10.4 с после изоляции) **продолжал принимать записи** (в 20:53:22.7 моя прямая запись на изолированный `pg1` прошла, `in_recovery=false`); затем Patroni потерял DCS и демоутил узел (`the database system is shutting down` — перезапуск в режиме реплики), записи прекратились |
| появился ли второй лидер | **нет**: снаружи кластер показывал `pg1=leader` до 20:53:35, новый лидер `pg2` появился в 20:53:41 (≈ 23 с после изоляции, по истечении `ttl`); к этому моменту `pg1` уже ≈ 13 с как не принимал записи — окна с двумя записываемыми узлами нет |
| что именно предотвратило split brain | порядок таймаутов: самодемоут изолированного лидера (`retry_timeout` = 10 с) **раньше**, чем истекает его ключ в etcd (`ttl` = 30 с) и кто-то другой может его взять; плюс лидер доступен клиентам только через HAProxy, который `/primary` у недоступного узла не пройдёт. Watchdog не настроен (`Could not activate Linux watchdog device` в логе Patroni) — при зависании самого процесса Patroni защиты не было бы |

Важно: запись, принятая изолированным лидером в первые ~10 с, **будет потеряна** (после возврата `pg1` перематывается `pg_rewind` и отбрасывает свою ветку): клиентам HAProxy подтверждений от него не поступало
(их соединения подвисли с `lat_ms=10290`), поэтому по подтверждённым транзакциям потерь нет (12 945 из 12 945). Чтобы изолированный лидер вообще не мог подтверждать записи, нужен `synchronous_mode_strict`.


---

## Дополнительные измерения для итоговой таблицы

**Э9. `DROP TABLE` / логическая ошибка.** Реплики не помогают: ошибка реплицируется мгновенно.

```bash
python (см. docs/failover_raw/e9_drop_table.txt): создать таблицу на лидере → убедиться, что она есть на реплике → DROP TABLE на лидере → ждать исчезновения на реплике
```

```
до: таблица important_data на реплике 172.19.0.6/32: 100000 строк
DROP TABLE выполнен на лидере; на реплике таблица исчезла через 0.4 мс — репликация доставила и ошибку
команды для проверки на всех узлах: patronictl list; psql -c "select to_regclass('important_data')" на pg1/pg2/pg3 -> NULL везде
```

**Э10. «ЦОД целиком» (холодный старт).** Все 9 контейнеров останавливаются (имитация потери питания), тома с данными сохраняются, затем всё запускается.

```bash
docker compose stop && sleep 3 && docker compose start
```

```
20:55:38.192 UTC: команда: docker compose stop   (все 9 контейнеров: имитация потери питания всего ЦОД; тома с данными сохраняются)
 Container hw01-etcd1-1 Stopped 
 Container hw01-etcd3-1 Stopped 
20:55:44.699 UTC: команда: docker compose start
 Container hw01-pgbouncer-ro-1 Started 
20:55:57.211 UTC: ПЕРВАЯ УСПЕШНАЯ ЗАПИСЬ через пул (:6432), id=1332492; холодный старт занял 1.0 с
кластер после старта: pg1=replica/running, pg2=leader/running, pg3=replica/running

итого: от команды docker compose start (20:55:44.699) до первой успешной записи (20:55:57.211) прошло 12.5 с
(сама команда docker compose start возвращается после запуска контейнеров с учётом depends_on/healthcheck).
```

Цифра 12.5 с (≈ 11.5 с блокируется сама команда `docker compose start` из-за `depends_on`/healthcheck, ещё ≈ 1 с до первой записи) — это возврат стенда **на тех же дисках**; она не отвечает на вопрос «ЦОД потерян безвозвратно»: все узлы и etcd стоят на одном хосте, второго ЦОД в стенде нет (см. вывод ниже).

---

## Итоговая таблица: отказ → кто чинит → цена

Заполнено **своими** числами из экспериментов выше.

| отказ | кто чинит | измеренная цена |
|-------|-----------|-----------------|
| много подключений | PgBouncer (пул, очередь клиентов) | без пула отказ уже на **100** клиентах (`remaining connection slots are reserved…`); с пулом 900 клиентов без ошибок, но очередь: медиана установки 1.28 с, p95 7.36 с при 900; на одиночном подключении пул дороже: **+8.3 мс** (13.8 vs 5.5 мс); ограничения transaction pooling (Э7) |
| упал узел БД (лидер) | Patroni + etcd (выбор лидера по истечении `ttl`) + HAProxy (перенаправление) | **RTO 25–33 с** (25.22 / 29.95 / 32.73 / 33.11 с); 876–2 100 отклонённых попыток записи на 4 потока; плановое переключение — **4.69 с** (в 5.4 раза дешевле), но при полной нагрузке отклоняется (Э4а); возврат узла — 3.2–12.7 с + `pg_rewind` ≈ 1 с |
| клиент не знает нового адреса | HAProxy (`:5400` всегда ведёт на лидера) + PgBouncer (`:6432`) | клиентский адрес не меняется, но клиент обязан переподключаться: ≈ 3.1 с уходит на обнаружение нового лидера HAProxy (`inter 2s fall 3 rise 2`) внутри общего RTO; соединения при смене лидера рвутся (`server closed the connection unexpectedly`) |
| потеря последних транзакций | `synchronous_mode` (Patroni, синхронная репликация) | async: **потеряно 2 748** подтверждённых транзакций из 146 981 (сценарий Э5); sync: **0**; цена sync: **−16.3 % TPS** (3 525 → 2 950), **+0.19 мс** p95 и блокировка записи (0.79 с в опыте), пока синхронная реплика недоступна |
| `DROP TABLE` / логическая ошибка | **не чинит никто в этом стенде**: нужны бэкапы + PITR (pgBackRest / WAL-G) — **не реализованы** | репликация доставила `DROP TABLE` на реплику за **0.4 мс** (Э9): реплики и failover защищают от отказов железа, но не от ошибок логики. Цена восстановления не измерена — механизма нет |
| ЦОД целиком | в стенде — только холодный перезапуск на тех же дисках; настоящее решение (реплика и кворум etcd в другом ЦОД) **не реализовано** | холодный старт всего стенда: **12.5 с** до первой успешной записи (Э10). Все 3 PostgreSQL и 3 etcd на одном хосте, поэтому потеря хоста = потеря данных; для ЦОД нужна асинхронная реплика в другом ЦОД и etcd как минимум с одним узлом вне ЦОД — отдельная конфигурация и РПО > 0 |

## Что бы вы поменяли в конфигурации после этих измерений

1. **Включить `synchronous_mode: true` (а для чеков и оплат — и `synchronous_mode_strict: true`).** Основание: в Э5 асинхронный режим потерял **2 748 подтверждённых** транзакций при падении лидера, синхронный — 0, а цена измерена и умеренна:
   −16.3 % TPS и +0.19 мс на p95 (при 3.5 тыс. TPS — 2.9 тыс.; реальная нагрузка сети аптек — десятки TPS, запас огромный). `strict` дополнительно закрывает окно Э8, когда изолированный лидер ещё ~10 с принимал записи, которые затем отбрасываются.
2. **Сократить RTO: `ttl` 30 → 15, `loop_wait` 10 → 5, `retry_timeout` 10 → 5 и `inter 1s` в HAProxy.** Основание: RTO аварии 25–33 с складывается из остатка `ttl` (≈ 22 с) и ≈ 3 с обнаружения HAProxy (Э2); при `ttl` = 15 остаток составит ≈ 10–15 с, при `inter 1s` обнаружение — ≈ 1.5 с,
   итого ориентировочно 12–17 с. Ограничение Patroni: `loop_wait + 2·retry_timeout ≤ ttl` (5 + 2·5 = 15 — выполнено). Цена: больше запросов к etcd и больше ложных переключений при кратковременных сетевых провалах (Э6 показал, что 10-секундный провал DCS уже демоутит лидера, при 5 с порог станет ещё чувствительнее) — поэтому менять только вместе с проверкой на реальной сети.
3. **Увеличить `maximum_lag_on_failover` до 16–32 МБ (и/или переключать при пониженной нагрузке) и добавить бэкапы с PITR.** Основание: в Э4а плановый `switchover` был **отклонён** («no good candidates»), потому что при 3.5 тыс. TPS за 10 с (интервал обновления позиции реплики в DCS) набегает 6.93 МБ WAL при пороге 1 МБ;
   а Э9 показал, что реплики доставляют `DROP TABLE` за 0.4 мс, то есть от логических ошибок защиты в стенде нет вообще — нужны pgBackRest/WAL-G с архивом WAL (PITR). Цена первого пункта — допустимое окно потери при аварийном failover растёт с 1 МБ до значения порога (но при `synchronous_mode` из п. 1 это уже не важно); цена второго — место под архив и операционная сложность.
