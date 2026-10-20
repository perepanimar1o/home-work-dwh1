#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Write-loop для экспериментов docs/failover_journal.md.

  python scripts/probe_write.py --seconds 60 --threads 4 --log /tmp/e1.log --acks /tmp/e1.acks   # нагрузка
  python scripts/probe_write.py --verify /tmp/e1.acks                                            # потеряны ли подтверждённые транзакции

Каждый поток в цикле: INSERT ... RETURNING id + COMMIT. Успехом считается только успешный COMMIT
(то есть «подтверждённая» транзакция). При ошибке поток закрывает соединение и пробует снова —
как это делал бы клиент без знания о смене лидера. Время фиксируется с точностью до миллисекунд
в UTC, чтобы сопоставлять с логами Patroni.
"""
import argparse
import datetime as dt
import statistics
import sys
import threading
import time

import psycopg2

DEFAULT_DSN = "postgresql://postgres:postgres@localhost:5400/postgres"
DDL = """
CREATE TABLE IF NOT EXISTS probe_writes (
    id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    worker  integer NOT NULL,
    seq     bigint  NOT NULL,
    ts      timestamptz NOT NULL DEFAULT clock_timestamp()
)"""


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def fmt(t: dt.datetime) -> str:
    return t.strftime("%H:%M:%S.") + f"{t.microsecond // 1000:03d}"


class Recorder:
    def __init__(self, log_path):
        self.lock = threading.Lock()
        self.events = []        # (t_end, worker, ok, latency_s, id_or_msg)
        self.fh = open(log_path, "w", encoding="utf-8") if log_path else None

    def add(self, worker, ok, latency, payload):
        t = now_utc()
        with self.lock:
            self.events.append((t, worker, ok, latency, payload))
            if self.fh:
                line = f"{fmt(t)} w{worker} {'OK ' if ok else 'ERR'} lat_ms={latency * 1000:.1f} {payload}\n"
                self.fh.write(line)


def worker_loop(wid, dsn, end, rec, sleep_s, connect_timeout, read_only=False):
    conn, seq = None, 0
    while time.time() < end:
        t0 = time.perf_counter()
        try:
            if conn is None or conn.closed:
                conn = psycopg2.connect(dsn, connect_timeout=connect_timeout)
                conn.autocommit = False
            seq += 1
            with conn.cursor() as cur:
                if read_only:
                    cur.execute("SELECT max(id) FROM probe_writes")
                else:
                    cur.execute("INSERT INTO probe_writes (worker, seq) VALUES (%s, %s) RETURNING id", (wid, seq))
                rid = cur.fetchone()[0]
            conn.commit()
            rec.add(wid, True, time.perf_counter() - t0, f"id={rid}")
        except Exception as e:  # noqa: BLE001
            msg = str(e).strip().splitlines()[0][:140] if str(e).strip() else type(e).__name__
            rec.add(wid, False, time.perf_counter() - t0, msg)
            try:
                if conn is not None:
                    conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn = None
            time.sleep(0.05)
        if sleep_s:
            time.sleep(sleep_s)


def summarize(rec: Recorder, wall: float, acks_path):
    ev = sorted(rec.events, key=lambda e: e[0])
    ok = [e for e in ev if e[2]]
    err = [e for e in ev if not e[2]]
    print(f"\nитог: длительность {wall:.1f} с, успешных транзакций {len(ok)}, ошибок {len(err)}")
    if ok:
        lat = sorted(e[3] * 1000 for e in ok)
        p95 = lat[int(0.95 * (len(lat) - 1))]
        print(f"      TPS (успешных/с) = {len(ok) / wall:.1f};  задержка записи: медиана {statistics.median(lat):.2f} мс, p95 {p95:.2f} мс")
    # окно недоступности записи = самый длинный промежуток между соседними успехами
    if len(ok) >= 2:
        gaps = [(ok[i + 1][0] - ok[i][0], ok[i][0], ok[i + 1][0]) for i in range(len(ok) - 1)]
        g, a, b = max(gaps)
        if g.total_seconds() > 1.0:
            print(f"      окно недоступности записи (RTO): {g.total_seconds():.2f} с  "
                  f"(последняя успешная запись {fmt(a)}, первая успешная после {fmt(b)} UTC)")
            during = [e for e in err if a <= e[0] <= b]
            print(f"      отклонено попыток в окне: {len(during)}")
            # выдержка из лога вокруг провала: последние успехи, первые/последние ошибки, первые успехи после
            def line(e):
                return f"{fmt(e[0])} w{e[1]} {'OK ' if e[2] else 'ERR'} lat_ms={e[3] * 1000:.1f} {e[4]}"
            before = [e for e in ok if e[0] <= a][-3:]
            after = [e for e in ok if e[0] >= b][:3]
            print("      --- фрагмент write-loop вокруг провала ---")
            for e in before:
                print("      " + line(e))
            shown = during[:5] + ([None] if len(during) > 10 else []) + during[-5:] if len(during) > 10 else during
            for e in shown:
                print("      " + (line(e) if e else "      ... (ещё ошибки) ..."))
            for e in after:
                print("      " + line(e))
        else:
            print(f"      провалов записи дольше 1 с нет (макс. промежуток между успехами {g.total_seconds() * 1000:.0f} мс)")
    if err:
        counts = {}
        for e in err:
            key = e[4]
            first, last, n = counts.get(key, (e[0], e[0], 0))
            counts[key] = (first, e[0], n + 1)
        print("      ошибки:")
        for msg, (first, last, n) in sorted(counts.items(), key=lambda kv: kv[1][0]):
            print(f"        x{n:<5} {fmt(first)} .. {fmt(last)}  {msg}")
    if acks_path:
        with open(acks_path, "w", encoding="utf-8") as f:
            for e in ok:
                f.write(e[4].split("=")[1] + "\n")
        print(f"      подтверждённые id сохранены: {acks_path} ({len(ok)})")


def verify(acks_path, dsn):
    ids = [int(x) for x in open(acks_path, encoding="utf-8").read().split()]
    conn = psycopg2.connect(dsn, connect_timeout=5)
    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM probe_writes WHERE id = ANY(%s)", (ids,))
        present = cur.fetchone()[0]
        cur.execute("SELECT pg_is_in_recovery(), inet_server_addr()::text")
        rec, addr = cur.fetchone()
    conn.close()
    lost = len(ids) - present
    print(f"проверка на узле {addr} (in_recovery={rec}): подтверждено клиенту {len(ids)}, найдено в таблице {present}, "
          f"ПОТЕРЯНО подтверждённых транзакций: {lost}")
    return 0 if lost == 0 else 3


def main():
    ap = argparse.ArgumentParser(description="write-loop для экспериментов отказоустойчивости")
    ap.add_argument("--dsn", default=DEFAULT_DSN)
    ap.add_argument("--seconds", type=float, default=60)
    ap.add_argument("--threads", type=int, default=4)
    ap.add_argument("--sleep-ms", type=float, default=0, help="пауза между транзакциями одного потока")
    ap.add_argument("--connect-timeout", type=int, default=2)
    ap.add_argument("--log")
    ap.add_argument("--acks")
    ap.add_argument("--verify", metavar="ACKS_FILE")
    ap.add_argument("--read", action="store_true", help="вместо записи — чтение (SELECT max(id)); для проверки доступности реплик")
    args = ap.parse_args()
    if args.verify:
        return verify(args.verify, args.dsn)

    if not args.read:                       # DDL выполняется на лидере; читающий режим работает с готовой таблицей
        conn = psycopg2.connect(args.dsn, connect_timeout=5)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(DDL)
        conn.close()

    rec = Recorder(args.log)
    end = time.time() + args.seconds
    t0 = time.time()
    print(f"{fmt(now_utc())} UTC: старт, {args.threads} потоков, {args.seconds:.0f} с, dsn={args.dsn.split('@')[-1]}", flush=True)
    threads = [threading.Thread(target=worker_loop, args=(i + 1, args.dsn, end, rec, args.sleep_ms / 1000, args.connect_timeout, args.read))
               for i in range(args.threads)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    summarize(rec, time.time() - t0, None if args.read else args.acks)
    return 0


if __name__ == "__main__":
    sys.exit(main())
