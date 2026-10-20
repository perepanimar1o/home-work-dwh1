#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Эксперимент Э7: наплыв клиентов. N потоков одновременно открывают соединение и держат запрос pg_sleep(hold).

  python scripts/connection_storm.py --dsn postgresql://app:app@localhost:5400/pos_service_db --ramp 50,90,100,150,300
  python scripts/connection_storm.py --dsn postgresql://app:app@localhost:6432/pos_service_db --ramp 50,90,100,150,300

5400 — напрямую в PostgreSQL через HAProxy (max_connections = 100), 6432 — через PgBouncer.
"""
import argparse
import collections
import statistics
import threading
import time

import psycopg2


def one(dsn, hold, out, barrier):
    barrier.wait()
    t0 = time.perf_counter()
    try:
        conn = psycopg2.connect(dsn, connect_timeout=30)
        t_conn = time.perf_counter() - t0
        with conn.cursor() as cur:
            cur.execute("SELECT pg_sleep(%s)", (hold,))
        conn.close()
        out.append((True, t_conn, None))
    except Exception as e:  # noqa: BLE001
        out.append((False, time.perf_counter() - t0, str(e).strip().splitlines()[0][:130]))


def run(dsn, n, hold):
    out, barrier = [], threading.Barrier(n)
    threads = [threading.Thread(target=one, args=(dsn, hold, out, barrier)) for _ in range(n)]
    t0 = time.time()
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    ok = [o for o in out if o[0]]
    errs = collections.Counter(o[2] for o in out if not o[0])
    t_conn = sorted(o[1] * 1000 for o in ok)
    row = dict(n=n, ok=len(ok), failed=n - len(ok), wall=time.time() - t0, errs=errs)
    if t_conn:
        row.update(median=statistics.median(t_conn), p95=t_conn[int(0.95 * (len(t_conn) - 1))])
    return row


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--ramp", default="50,90,100,150,300")
    ap.add_argument("--hold", type=float, default=1.0, help="сколько секунд клиент держит запрос")
    args = ap.parse_args()
    print(f"цель: {args.dsn.split('@')[-1]}; каждый клиент держит pg_sleep({args.hold}) и закрывает соединение")
    print(f"{'клиентов':>9} {'успешно':>8} {'ошибок':>7} {'время установки соединения, мс (медиана / p95)':>47} {'всего, с':>9}")
    for n in [int(x) for x in args.ramp.split(",")]:
        r = run(args.dsn, n, args.hold)
        conn = f"{r['median']:.1f} / {r['p95']:.1f}" if "median" in r else "-"
        print(f"{r['n']:>9} {r['ok']:>8} {r['failed']:>7} {conn:>47} {r['wall']:>9.1f}")
        for msg, c in r["errs"].most_common(2):
            print(f"{'':>9} ошибка x{c}: {msg}")
        time.sleep(2)


if __name__ == "__main__":
    main()
