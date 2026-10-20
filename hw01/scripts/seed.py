#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор данных ДЗ №1 (сеть аптек «Аптека 108»).

    python scripts/seed.py --seed 42                 # базовая загрузка (идемпотентна: повтор с тем же seed ничего не делает)
    python scripts/seed.py --seed 777 --receipts 500 # догенерация 500 чеков поверх существующих данных
    python scripts/seed.py --seed 42 --dry-run       # только посчитать и показать статистику

Свойства:
  * детерминированность: один и тот же --seed и одно и то же состояние БД дают одни и те же данные;
  * кросс-сервисная целостность: продаём только то, что есть в каталоге, только из партий, лежащих на
    складе аптеки (остаток пополняется перемещением из РЦ), только клиентам, которые есть в CRM;
  * один общий «мир» для четырёх БД: закупки -> приёмка на РЦ -> перемещения в аптеки -> продажи,
    и все остатки сходятся с журналом движений (триггер склада проверяет это на каждом шаге);
  * правдоподобные распределения (бонус): недельная сезонность, длинный хвост SKU (Zipf),
    неравномерность аптек (lognormal), доля покупок без карты ~45%, возвраты ~3%.
"""
import argparse
import bisect
import collections
import csv
import datetime as dt
import heapq
import io
import math
import os
import random
import sys
import time
from decimal import Decimal, ROUND_HALF_UP

import psycopg2

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import seed_data as D  # noqa: E402

UTC = dt.timezone.utc
MSK = dt.timezone(dt.timedelta(hours=3))
AS_OF_DEFAULT = dt.date(2026, 10, 20)
HISTORY_DAYS = 365
WARMUP_DAYS = 40            # закупки до начала истории, чтобы в день 1 на РЦ уже были остатки

N_SKU = 650
N_CUSTOMERS = 2500
N_PHARMACIES = 40
TARGET_RECEIPTS = 24500     # чеков (продажи + возвраты) за год
REFUND_RATE = 0.032
P_CARD = 0.57               # доля покупок по карте (остальные — без карты)
AVG_LINES, AVG_QTY = 2.9, 1.55
ZIPF_S = 0.9
WEEKDAY = {1: 1.00, 2: 0.95, 3: 0.95, 4: 1.00, 5: 1.15, 6: 1.45, 7: 0.70}   # isodow -> коэффициент

CENT = Decimal("0.01")


def money(x) -> Decimal:
    return Decimal(x).quantize(CENT, ROUND_HALF_UP)


def local_dt(day: dt.date, hour: int, minute: int = 0, second: int = 0) -> dt.datetime:
    """Локальное (МСК) время -> aware UTC."""
    return dt.datetime(day.year, day.month, day.day, hour, minute, second, tzinfo=MSK).astimezone(UTC)


def midnight(day: dt.date) -> dt.datetime:
    return dt.datetime(day.year, day.month, day.day, tzinfo=UTC)


# ============================================================================
# Справочные данные
# ============================================================================
class Ref:
    """Справочники мира. Собираются генератором (база) либо читаются из БД (догенерация)."""
    pass


def gen_reference(seed: int, as_of: dt.date) -> Ref:
    rng = random.Random(f"{seed}:reference")
    start = as_of - dt.timedelta(days=HISTORY_DAYS)
    ref = Ref()
    ref.as_of, ref.start = as_of, start

    # --- производители, формы, МНН ---
    ref.manufacturers = [(f"MFR-{i + 1:04d}", n, c) for i, (n, c) in enumerate(D.MANUFACTURERS)]
    ref.drug_forms = list(D.DRUG_FORMS)
    ref.inns = [(f"INN-{i + 1:04d}", n) for i, n in enumerate(D.INNS)]

    # --- SKU ---
    cats = ["rx"] * 28 + ["otc"] * 34 + ["supplement"] * 16 + ["device"] * 11 + ["cosmetics"] * 11
    pop_mult = {"otc": 1.6, "cosmetics": 1.2, "supplement": 1.4, "rx": 1.0, "device": 0.5}
    price_mult = {"device": 3.5, "cosmetics": 1.4, "supplement": 1.5, "rx": 1.6, "otc": 0.8}
    skus = []
    for i in range(1, N_SKU + 1):
        cat = rng.choice(cats)
        pack = rng.choice(D.PACKS) if cat in ("rx", "otc", "supplement") else 1
        inn_idx, form = [], None
        if cat in ("rx", "otc"):
            inn_idx = [rng.randrange(len(D.INNS))]
            if rng.random() < 0.08:
                inn_idx += rng.sample(range(len(D.INNS)), rng.choice([1, 2]))
            inn_idx = sorted(set(inn_idx))
            form = rng.choice(D.DRUG_FORMS)
            dose = rng.choice(D.DOSES)
            inn_name = D.INNS[inn_idx[0]].capitalize()
            if rng.random() < 0.5:
                brand = (rng.choice(D.BRAND_PREFIX) + rng.choice(D.BRAND_SUFFIX)).capitalize()
                name = f"{brand} ({D.INNS[inn_idx[0]]}) {dose} {form[1]} №{pack}"
            else:
                name = f"{inn_name} {dose} {form[1]} №{pack}"
        elif cat == "supplement":
            form = rng.choice(D.DRUG_FORMS[:2])
            name = f"{rng.choice(D.SUPPLEMENTS)} {rng.choice(D.LINES)} {form[1]} №{pack}"
        elif cat == "device":
            name = f"{rng.choice(D.DEVICES)} {rng.choice(D.LINES)}"
        else:
            name = f"{rng.choice(D.COSMETICS)} {rng.choice(D.LINES)} {rng.choice([30, 50, 75, 100, 150, 250])} мл"
        price = money(min(9000, max(30, rng.lognormvariate(math.log(260), 0.85) * price_mult[cat])))
        skus.append(dict(
            bk=f"SKU-{i:06d}", name=name, category=cat, is_rx=(cat == "rx"),
            is_vital=(cat in ("rx", "otc") and rng.random() < 0.12), pack=Decimal(pack),
            mfr=rng.choice(ref.manufacturers)[0], form=(form[0] if form else None),
            inn_idx=inn_idx, base_price=price,
            pop=rng.random() ** 1.0 / pop_mult[cat], supplier=rng.randrange(len(D.SUPPLIERS)),
        ))
    # популярность: чем меньше pop, тем выше ранг; вес по Zipf
    order = sorted(range(N_SKU), key=lambda k: skus[k]["pop"])
    for rank, k in enumerate(order, start=1):
        skus[k]["rank"] = rank
        skus[k]["weight"] = 1.0 / rank ** ZIPF_S
    wsum = sum(s["weight"] for s in skus)
    for s in skus:
        s["weight"] /= wsum

    # --- атрибуты, которые меняются: переименования и перевод rx -> otc ---
    created = midnight(start - dt.timedelta(days=400))
    tail = [s for s in skus if s["rank"] > 150]
    for s in skus:
        s["versions"] = [dict(vf=created, name=s["name"], category=s["category"], is_rx=s["is_rx"], is_vital=s["is_vital"])]
    for s in rng.sample([s for s in skus if s["rank"] > 60], 33):          # ~5% переименований
        when = midnight(start + dt.timedelta(days=rng.randint(40, HISTORY_DAYS - 40)))
        old = s["versions"][0]
        old["name"] = old["name"] + " (прежнее название)"
        s["versions"].append(dict(vf=when, name=s["name"], category=s["category"], is_rx=s["is_rx"], is_vital=s["is_vital"]))
    rx_tail = [s for s in tail if s["category"] == "rx" and len(s["versions"]) == 1]
    for s in rng.sample(rx_tail, min(10, len(rx_tail))):                    # препарат стал безрецептурным
        when = midnight(start + dt.timedelta(days=rng.randint(60, HISTORY_DAYS - 60)))
        old = s["versions"][0]
        s["versions"].append(dict(vf=when, name=old["name"], category="otc", is_rx=False, is_vital=old["is_vital"]))
        s["category"], s["is_rx"] = "otc", False
    for s in skus:
        s["flip_dt"] = next((v["vf"] for v in s["versions"][1:] if v["is_rx"] != s["versions"][0]["is_rx"]), None)
    ref.skus = skus
    ref.sku_by_bk = {s["bk"]: s for s in skus}

    # --- регионы, РЦ, поставщики ---
    ref.regions = D.REGIONS
    ref.region_dc = {r[0]: r[3] for r in D.REGIONS}
    ref.dcs = list(D.DCS)
    ref.suppliers = [(f"SUP-{i + 1:04d}", n, c) for i, (n, c) in enumerate(D.SUPPLIERS)]

    # --- аптеки ---
    nums = sorted(rng.sample(range(100, 700), N_PHARMACIES))
    reg_w = [r[2] for r in D.REGIONS]
    regions = [D.REGIONS[i] for i in range(len(D.REGIONS))] + rng.choices(D.REGIONS, reg_w, k=N_PHARMACIES - len(D.REGIONS))
    rng.shuffle(regions)
    pharmacies = []
    for idx, (num, reg) in enumerate(zip(nums, regions)):
        city = rng.choice(reg[1])
        opened = start - dt.timedelta(days=rng.randint(60, 4000))
        closed = None
        if idx in (7, 21):
            closed = start + dt.timedelta(days=rng.randint(150, HISTORY_DAYS - 30))
        if idx in (3, 11, 25, 33):
            opened = start + dt.timedelta(days=rng.randint(20, 220))
        pharmacies.append(dict(
            bk=f"PH-{num:04d}", num=num, name=f"Аптека 108 №{num}", region=reg[0], city=city,
            address=f"{rng.choice(D.STREETS)} ул., д. {rng.randint(1, 120)}", opened=opened, closed=closed,
            dc=reg[3], weight=rng.lognormvariate(0, 0.5) * (reg[2] ** 0.3),
        ))
    ref.pharmacies = pharmacies
    ref.cashiers = {}
    cn = 0
    for p in pharmacies:
        lst = []
        for _ in range(4):
            cn += 1
            fem = rng.random() < 0.7
            name = (f"{rng.choice(D.LAST_M)}{'а' if fem else ''} {rng.choice(D.FIRST_F if fem else D.FIRST_M)} "
                    f"{rng.choice(D.PATRONYMIC_F if fem else D.PATRONYMIC_M)}")
            lst.append((f"CSH-{cn:06d}", name))
        ref.cashiers[p["bk"]] = lst

    # --- цены: chain / region / pharmacy ---
    ref.price_versions = []          # (sku_bk, scope, scope_bk, price, vf, vt)
    price_start = midnight(start - dt.timedelta(days=60))
    for s in skus:
        nv = rng.choice([2, 3, 3, 4])
        cuts = [midnight(start + dt.timedelta(days=d)) for d in sorted(rng.sample(range(20, HISTORY_DAYS - 20), nv - 1))]
        bounds = [price_start] + cuts
        price = s["base_price"]
        for k, vf in enumerate(bounds):
            vt = bounds[k + 1] if k + 1 < len(bounds) else None
            ref.price_versions.append((s["bk"], "chain", "ALL", price, vf, vt))
            step = rng.uniform(0.02, 0.09) if rng.random() < 0.85 else -rng.uniform(0.02, 0.06)
            price = max(money(1), money(price * Decimal(1 + step)))
    chain_idx = collections.defaultdict(list)
    for sku, scope, sbk, price, vf, vt in ref.price_versions:
        chain_idx[sku].append((vf, vt, price))

    def chain_price(sku, when):
        for vf, vt, pr in chain_idx[sku]:
            if vf <= when and (vt is None or when < vt):
                return pr
        return chain_idx[sku][-1][2]

    for s in skus:
        for reg in D.REGIONS:
            if rng.random() < 0.07:
                a = midnight(start + dt.timedelta(days=rng.randint(0, HISTORY_DAYS - 100)))
                versions = [a]
                if rng.random() < 0.5:
                    nxt = midnight(a.date() + dt.timedelta(days=rng.randint(30, 120)))
                    if nxt < midnight(as_of):
                        versions.append(nxt)
                for k, vf in enumerate(versions):
                    vt = versions[k + 1] if k + 1 < len(versions) else None
                    pr = money(chain_price(s["bk"], vf) * Decimal(rng.uniform(0.95, 1.07)))
                    ref.price_versions.append((s["bk"], "region", reg[0], pr, vf, vt))
        for p in pharmacies:
            if rng.random() < 0.01:
                vf = midnight(start + dt.timedelta(days=rng.randint(0, HISTORY_DAYS - 60)))
                pr = money(chain_price(s["bk"], vf) * Decimal(rng.uniform(0.96, 1.04)))
                ref.price_versions.append((s["bk"], "pharmacy", p["bk"], pr, vf, None))
    build_price_index(ref)

    # --- клиенты ---
    ref.customers = []
    reg_cities = [(r[0], c) for r in D.REGIONS for c in r[1]]
    reg_cw = [r[2] / len(r[1]) for r in D.REGIONS for _ in r[1]]
    for i in range(1, N_CUSTOMERS + 1):
        fem = rng.random() < 0.62
        region, city = rng.choices(reg_cities, reg_cw)[0]
        registered = midnight(as_of - dt.timedelta(days=rng.randint(30, 1100))) + dt.timedelta(seconds=rng.randint(28800, 72000))
        level = rng.choices(["basic", "silver", "gold", "platinum"], [55, 25, 15, 5])[0]
        c = dict(
            bk=f"CUST-{i:06d}",
            name=(f"{rng.choice(D.LAST_M)}{'а' if fem else ''} {rng.choice(D.FIRST_F if fem else D.FIRST_M)} "
                  f"{rng.choice(D.PATRONYMIC_F if fem else D.PATRONYMIC_M)}"),
            birth_year=int(min(2008, max(1938, rng.gauss(1976, 16)))),
            sex=None if rng.random() < 0.08 else ("F" if fem else "M"),
            city=city, region=region, level=level, registered=registered,
            phone=f"+7{rng.randint(900, 999)}{rng.randint(1000000, 9999999)}",
            email=(f"user{i}@example.com" if rng.random() < 0.6 else None),
            marketing=rng.random() < 0.6, marketing_revoked=None,
            has_card=rng.random() < 0.72, reissued=False,
            history=None,
        )
        if c["marketing"] and rng.random() < 0.1:
            revoked = registered + dt.timedelta(days=rng.randint(30, 600))
            c["marketing_revoked"] = revoked if revoked < midnight(as_of) else None   # события из «будущего» не генерируем
        if rng.random() < 0.15 and level != "basic":                   # раньше был ниже
            when = registered + dt.timedelta(days=rng.randint(40, 700))
            if when < midnight(as_of):
                prev = {"silver": "basic", "gold": "silver", "platinum": "gold"}[level]
                c["history"] = (registered, when, city, region, prev)
        elif rng.random() < 0.03:                                       # переехал
            when = registered + dt.timedelta(days=rng.randint(40, 700))
            if when < midnight(as_of):
                oreg, ocity = rng.choice(reg_cities)
                c["history"] = (registered, when, ocity, oreg, level)
        c["updated"] = c["history"][1] if c["history"] else registered
        c["reissued"] = c["has_card"] and rng.random() < 0.08
        ref.customers.append(c)
    return ref


def build_price_index(ref: Ref) -> None:
    """(scope, scope_bk, sku) -> отсортированные версии (vf, vt, price)."""
    idx = collections.defaultdict(list)
    for sku, scope, sbk, price, vf, vt in ref.price_versions:
        idx[(scope, sbk, sku)].append((vf, vt, price))
    for v in idx.values():
        v.sort(key=lambda t: t[0])
    ref.price_idx = idx
    ref.price_vf = {k: [t[0] for t in v] for k, v in idx.items()}


def price_at(ref: Ref, sku: str, ph: dict, when: dt.datetime) -> Decimal:
    """Узкая область приоритетнее широкой: аптека > регион > сеть."""
    for scope, sbk in (("pharmacy", ph["bk"]), ("region", ph["region"]), ("chain", "ALL")):
        key = (scope, sbk, sku)
        vf = ref.price_vf.get(key)
        if not vf:
            continue
        i = bisect.bisect_right(vf, when) - 1
        if i >= 0:
            _, vt, pr = ref.price_idx[key][i]
            if vt is None or when < vt:
                return pr
    raise LookupError(f"нет цены {sku} @ {when}")


def sku_state_at(ref: Ref, sku: dict, when: dt.datetime):
    """(название, is_rx) на момент when."""
    v = sku["versions"][0]
    for cand in sku["versions"]:
        if cand["vf"] <= when:
            v = cand
    return v["name"], v["is_rx"]


# ============================================================================
# Склад и продажи: общий «мир»
# ============================================================================
class Batch:
    __slots__ = ("idx", "bk", "sku", "series", "mfg", "exp", "received", "supplier", "qty0", "db_id")

    def __init__(self, **kw):
        for k, v in kw.items():
            setattr(self, k, v)


class Sim:
    def __init__(self, ref: Ref, rng: random.Random, as_of: dt.date, counters=None):
        self.ref, self.rng, self.as_of = ref, rng, as_of
        self.batches = []
        self.dc_stock = collections.defaultdict(lambda: collections.defaultdict(list))   # dc -> sku -> [[batch, qty]]
        self.ph_stock = collections.defaultdict(lambda: collections.defaultdict(list))
        self.dc_avail = collections.defaultdict(int)       # (dc, sku) -> остаток + заказано
        self.arrivals = []                                  # куча (arrival_dt, seq, dc, sku, qty, po_ref)
        self.expiring = collections.defaultdict(list)       # дата -> [batch]
        self.batch_locs = collections.defaultdict(set)
        self.moves = []                                     # dict
        self.po_list = []                                   # заказы
        self.po_lines = []                                  # строки заказов
        self.pending_sales = collections.defaultdict(int)   # (ph, batch.idx, iso_week) -> qty
        self.sales = []                                     # чеки-продажи
        self.seq = 0
        self.counters = counters or {}
        self.batch_year_n = collections.defaultdict(int, self.counters.get("batch_year_n", {}))
        self.series_used = set()
        self.ph_by_bk = {p["bk"]: p for p in ref.pharmacies}
        # спрос на пару (РЦ, SKU), упаковок в день
        dc_w = collections.defaultdict(float)
        for p in ref.pharmacies:
            dc_w[p["dc"]] += p["weight"]
        tot = sum(dc_w.values())
        self.dc_share = {d: w / tot for d, w in dc_w.items()}
        daily_packs = (TARGET_RECEIPTS / (HISTORY_DAYS * (1 + REFUND_RATE))) * AVG_LINES * AVG_QTY
        self.lam = {(d[0], s["bk"]): daily_packs * s["weight"] * self.dc_share.get(d[0], 0)
                    for d in ref.dcs for s in ref.skus}
        cum, acc = [], 0.0
        for s in ref.skus:
            acc += s["weight"]
            cum.append(acc)
        self.sku_cum, self.sku_total = cum, acc

    # ---------------------------------------------------------------- вспомогательное
    def _move(self, mtype, batch, src, dst, qty, moved_at, dispatched_at=None, counterparty=None):
        self.seq += 1
        self.moves.append(dict(seq=self.seq, type=mtype, batch=batch, src=src, dst=dst, qty=qty,
                               moved_at=moved_at, dispatched_at=dispatched_at, counterparty=counterparty))

    @staticmethod
    def _insert_fefo(lst, batch, qty):
        keys = [e[0].exp for e in lst]
        lst.insert(bisect.bisect_right(keys, batch.exp), [batch, qty])

    def _new_batch(self, sku, arrival: dt.datetime, supplier_bk):
        rng = self.rng
        year = arrival.year
        self.batch_year_n[year] += 1
        mfg = arrival.date() - dt.timedelta(days=rng.randint(20, 240))
        if rng.random() < 0.04:
            exp = arrival.date() + dt.timedelta(days=rng.randint(25, 120))        # короткий срок: уйдёт в списание
        else:
            months = rng.choices([12, 18, 24, 36, 48], [10, 15, 35, 30, 10])[0]
            exp = mfg + dt.timedelta(days=int(months * 30.4))
            if exp < arrival.date() + dt.timedelta(days=180):
                exp = arrival.date() + dt.timedelta(days=rng.randint(180, 400))
        while True:
            series = f"{rng.choice('ABCDEFGHKLMNPRSTUVXZ')}{rng.randint(10000, 99999)}"
            if (sku, series) not in self.series_used:
                self.series_used.add((sku, series))
                break
        b = Batch(idx=len(self.batches), bk=f"BATCH-{year}-{self.batch_year_n[year]:05d}", sku=sku, series=series,
                  mfg=mfg, exp=exp, received=arrival, supplier=supplier_bk, qty0=0, db_id=None)
        self.batches.append(b)
        self.expiring[exp].append(b)
        return b

    # ---------------------------------------------------------------- закупки
    def place_orders(self, day: dt.date, initial=False):
        """Пополнение РЦ: пара (РЦ, SKU) заказывается, когда остаток + заказанное опустились до точки заказа."""
        rng, ref = self.rng, self.ref
        grouped = collections.defaultdict(list)
        for dc in ref.dcs:
            for s in ref.skus:
                key = (dc[0], s["bk"])
                lam = self.lam[key]
                if lam <= 0:
                    continue
                lead_max = 9
                rop = math.ceil(lam * (lead_max + 7))
                if initial:
                    need = max(12, math.ceil(lam * 45))
                elif self.dc_avail[key] <= rop:
                    need = max(12, math.ceil(lam * 30))
                else:
                    continue
                supplier = ref.suppliers[s["supplier"] if rng.random() < 0.85 else rng.randrange(len(ref.suppliers))][0]
                grouped[(supplier, dc[0])].append((s, need))
                self.dc_avail[key] += need
        for (supplier, dc), items in sorted(grouped.items()):
            ordered_at = local_dt(day, 10, rng.randint(0, 59))
            po = dict(supplier=supplier, dc=dc, ordered_at=ordered_at, lines=[])
            self.po_list.append(po)
            for line_no, (s, qty) in enumerate(items, start=1):
                lead = rng.randint(3, 9)
                arrival = local_dt(day + dt.timedelta(days=lead), rng.randint(9, 15), rng.randint(0, 59))
                cp = chain_price_now(self.ref, s["bk"], ordered_at)
                cost = money(cp * Decimal(rng.uniform(0.55, 0.75)))
                ln = dict(po=po, line_no=line_no, sku=s["bk"], qty=Decimal(qty), cost=cost,
                          arrival=arrival, batch=None)
                po["lines"].append(ln)
                self.po_lines.append(ln)
                heapq.heappush(self.arrivals, (arrival, len(self.po_lines), ln))

    def process_arrivals(self, upto: dt.datetime):
        while self.arrivals and self.arrivals[0][0] <= upto:
            arrival, _, ln = heapq.heappop(self.arrivals)
            b = self._new_batch(ln["sku"], arrival, ln["po"]["supplier"])
            b.qty0 = int(ln["qty"])
            ln["batch"] = b
            dc = ln["po"]["dc"]
            self._insert_fefo(self.dc_stock[dc][ln["sku"]], b, b.qty0)
            self.batch_locs[b.idx].add(("dc", dc))
            self._move("receipt", b, ("supplier", ln["po"]["supplier"]), ("dc", dc), b.qty0, arrival,
                       dispatched_at=ln["po"]["ordered_at"] + dt.timedelta(days=1))

    # ---------------------------------------------------------------- списания
    def process_expiry(self, day: dt.date):
        """Срок вышел вчера: остатки на РЦ и в аптеках списываются."""
        rng = self.rng
        for b in self.expiring.pop(day - dt.timedelta(days=1), []):
            for ltype, lbk in sorted(self.batch_locs.get(b.idx, ())):
                stock = (self.dc_stock if ltype == "dc" else self.ph_stock)[lbk][b.sku]
                for entry in list(stock):
                    if entry[0] is b and entry[1] > 0:
                        self._move("writeoff", b, (ltype, lbk), None, entry[1], local_dt(day, 3, rng.randint(0, 59)))
                        if ltype == "dc":
                            self.dc_avail[(lbk, b.sku)] -= entry[1]
                        entry[1] = 0
                        stock.remove(entry)
            self.batch_locs.pop(b.idx, None)

    def process_returns_to_supplier(self, day: dt.date):
        """Часть близкого к истечению товара с РЦ возвращается поставщику."""
        rng = self.rng
        target = day + dt.timedelta(days=90)
        for b in self.expiring.get(target, []):
            if rng.random() > 0.6:
                continue
            for ltype, lbk in sorted(self.batch_locs.get(b.idx, ())):
                if ltype != "dc":
                    continue
                for entry in self.dc_stock[lbk][b.sku]:
                    if entry[0] is b and entry[1] >= 12:
                        q = int(entry[1] * 0.6)
                        self._move("return", b, ("dc", lbk), None, q, local_dt(day, 12, rng.randint(0, 59)),
                                   counterparty=b.supplier)
                        entry[1] -= q
                        self.dc_avail[(lbk, b.sku)] -= q

    # ---------------------------------------------------------------- продажи
    def _pick_sku(self):
        r = self.rng.random() * self.sku_total
        return self.ref.skus[min(bisect.bisect_left(self.sku_cum, r), len(self.ref.skus) - 1)]

    def _jit_transfer(self, ph: dict, sku: dict, need: int, day: dt.date):
        """Нет остатка в аптеке -> перемещение с РЦ (отгрузка за 1-3 дня, приёмка утром в день продажи)."""
        rng = self.rng
        dc = ph["dc"]
        moved_at = local_dt(day, 7, rng.randint(0, 40))
        delay = dt.timedelta(days=rng.choice([1, 1, 1, 2, 3]), hours=rng.randint(0, 5))
        dispatched = moved_at - delay
        horizon = day + dt.timedelta(days=45)
        for entry in self.dc_stock[dc][sku["bk"]]:
            b, qty = entry
            if qty < need or b.exp < horizon or b.received > dispatched:
                continue
            lot = rng.choice([4, 6, 8, 10, 12, 20])
            take = min(qty, max(need, lot))
            entry[1] -= take
            self.dc_avail[(dc, sku["bk"])] -= take
            if entry[1] == 0:
                self.dc_stock[dc][sku["bk"]].remove(entry)
            self._insert_fefo(self.ph_stock[ph["bk"]][sku["bk"]], b, take)
            self.batch_locs[b.idx].add(("pharmacy", ph["bk"]))
            self._move("transfer", b, ("dc", dc), ("pharmacy", ph["bk"]), take, moved_at, dispatched_at=dispatched)
            if rng.random() < 0.002:                                      # брак при приёмке
                self._move("writeoff", b, ("pharmacy", ph["bk"]), None, 1, moved_at + dt.timedelta(hours=1))
                for e in self.ph_stock[ph["bk"]][sku["bk"]]:
                    if e[0] is b:
                        e[1] -= 1
                        break
            return True
        return False

    def _sell_line(self, ph: dict, sku: dict, qty: int, day: dt.date):
        lst = self.ph_stock[ph["bk"]][sku["bk"]]
        for attempt in (0, 1):
            for entry in lst:
                b, have = entry
                if have >= qty and b.exp >= day:
                    entry[1] -= qty
                    week = day.isocalendar()[:2]
                    self.pending_sales[(ph["bk"], b.idx, week)] += qty
                    if entry[1] == 0:
                        lst.remove(entry)
                    return b
            if attempt == 0 and not self._jit_transfer(ph, sku, qty, day):
                return None
        return None

    def flush_sales(self, upto_day: dt.date, final=False):
        """Продажи за неделю списываются со склада аптеки одним движением на (аптека, партия)."""
        for key in sorted(list(self.pending_sales)):
            ph_bk, bidx, week = key
            week_end = dt.date.fromisocalendar(week[0], week[1], 7)
            if final or week_end < upto_day:
                when = local_dt(min(week_end, self.as_of), 23, 0)
                b = self.batches[bidx]
                self._move("sale_writeoff", b, ("pharmacy", ph_bk), ("customer", None), self.pending_sales.pop(key), when)

    def sell_day(self, day: dt.date, n_receipts: int):
        rng, ref = self.rng, self.ref
        active = [p for p in ref.pharmacies
                  if p["opened"] <= day and (p["closed"] is None or day <= p["closed"])]
        if not active or n_receipts <= 0:
            return
        weights = [p["weight"] for p in active]
        counts = collections.Counter(rng.choices(range(len(active)), weights, k=n_receipts))
        for pi, cnt in counts.items():
            ph = active[pi]
            for _ in range(cnt):
                self._one_sale(ph, day)

    def _one_sale(self, ph: dict, day: dt.date):
        rng, ref = self.rng, self.ref
        n_lines = rng.choices([1, 2, 3, 4, 5, 6, 7, 8], [25, 25, 20, 12, 8, 5, 3, 2])[0]
        hour = rng.choices(range(8, 22), [4, 7, 9, 9, 8, 6, 5, 5, 6, 8, 9, 8, 5, 2])[0]
        when = local_dt(day, hour, rng.randint(0, 59), rng.randint(0, 59))
        if hour == 8 and when < local_dt(day, 8, 5):
            when = local_dt(day, 8, 5)
        chosen, lines = set(), []
        for _ in range(n_lines):
            sku = self._pick_sku()
            if sku["bk"] in chosen:
                continue
            chosen.add(sku["bk"])
            qty = rng.choices([1, 2, 3, 4, 5, 6], [65, 22, 8, 3, 1, 1])[0]
            b = self._sell_line(ph, sku, qty, day)
            if b is None:
                continue
            name, is_rx = sku_state_at(ref, sku, when)
            lines.append(dict(sku=sku, batch=b, qty=qty, price=price_at(ref, sku["bk"], ph, when),
                              rx=is_rx, name=name, vital=sku["is_vital"]))
        if not lines:
            return
        self.sales.append(dict(ph=ph, when=when, lines=lines, kind="sale"))

    # ---------------------------------------------------------------- прогон
    def run_history(self, start: dt.date, scale: float):
        ref, rng = self.ref, self.rng
        d0 = start - dt.timedelta(days=WARMUP_DAYS)
        self.place_orders(d0, initial=True)
        days = [start + dt.timedelta(days=i) for i in range((self.as_of - start).days + 1)]
        raw = [WEEKDAY[d.isoweekday()] * (1 + 0.15 * math.sin(2 * math.pi * (d.timetuple().tm_yday - 330) / 365))
               * (1 + 0.1 * (d - start).days / HISTORY_DAYS) for d in days]
        per_unit = TARGET_RECEIPTS / (1 + REFUND_RATE) * scale / sum(raw)
        d = d0
        while d <= self.as_of:
            self.process_arrivals(local_dt(d, 9, 0))
            self.process_expiry(d)
            self.process_returns_to_supplier(d)
            if d >= start:
                lam = per_unit * raw[(d - start).days]
                n = max(0, int(round(rng.gauss(lam, math.sqrt(lam)))))
                self.sell_day(d, n)
                self.flush_sales(d)
            self.process_arrivals(local_dt(d, 23, 59))
            self.place_orders(d)
            d += dt.timedelta(days=1)
        self.flush_sales(self.as_of, final=True)
        # заказы, не пришедшие к as_of, остаются «в пути»
        pending = {id(ln) for _, _, ln in self.arrivals}
        for ln in self.po_lines:
            ln["in_transit"] = id(ln) in pending


def chain_price_now(ref: Ref, sku: str, when: dt.datetime) -> Decimal:
    v = ref.price_idx[("chain", "ALL", sku)]
    i = bisect.bisect_right(ref.price_vf[("chain", "ALL", sku)], when) - 1
    return v[max(i, 0)][2]


# ============================================================================
# Чеки: клиенты, скидки, оплаты, возвраты, нумерация
# ============================================================================
def finalize_receipts(ref: Ref, rng: random.Random, sim: Sim, start_numbers=None, shifts_existing=None,
                      customers_pool=None):
    """Превращает продажи в документы: клиенты, скидки, оплаты, рецепты, возвраты, BK и смены."""
    as_of = sim.as_of
    pool = customers_pool if customers_pool is not None else [c for c in ref.customers if c["has_card"]]
    cust_w, acc = [], 0.0
    order = list(range(len(pool)))
    rng.shuffle(order)
    for rank, k in enumerate(order, start=1):
        acc += 1.0 / rank ** 0.7
        cust_w.append((acc, k))
    cust_cum = [a for a, _ in cust_w]
    disc_pct = {"basic": Decimal("0.00"), "silver": Decimal("0.03"), "gold": Decimal("0.05"), "platinum": Decimal("0.08")}

    docs = []
    for sale in sim.sales:
        when = sale["when"]
        cust = None
        if rng.random() < P_CARD and pool:
            for _ in range(5):
                c = pool[cust_w[min(bisect.bisect_left(cust_cum, rng.random() * acc), len(pool) - 1)][1]]
                if c["registered"] + dt.timedelta(days=1) <= when:
                    cust = c
                    break
        pct = disc_pct[cust["level"]] if cust else Decimal("0.00")
        lines = []
        for ln in sale["lines"]:
            gross = money(ln["price"] * ln["qty"])
            disc = money(gross * pct) if (cust and not ln["vital"] and not ln["rx"]) else Decimal("0.00")
            lines.append(dict(ln, disc=disc, gross=gross, amount=gross - disc))
        total = sum((l["amount"] for l in lines), Decimal("0.00"))
        disc_total = sum((l["disc"] for l in lines), Decimal("0.00"))
        payments = split_payment(rng, total, has_card=cust is not None)
        docs.append(dict(kind="sale", ph=sale["ph"], when=when, cust=cust, lines=lines, total=total,
                         discount=disc_total, payments=payments, rx=any(l["rx"] for l in lines), parent=None))
    sales = list(docs)

    # возвраты
    for sdoc in sales:
        if rng.random() >= REFUND_RATE:
            continue
        day = sdoc["when"].astimezone(MSK).date() + dt.timedelta(days=rng.choice([0, 0, 1, 1, 2, 3, 5, 7]))
        ph = sdoc["ph"]
        if day > as_of or (ph["closed"] is not None and day > ph["closed"]):
            continue
        if day == sdoc["when"].astimezone(MSK).date():
            when = sdoc["when"] + dt.timedelta(minutes=rng.randint(10, 150))
            if when > local_dt(day, 21, 55):
                continue
        else:
            when = local_dt(day, rng.randint(9, 20), rng.randint(0, 59), rng.randint(0, 59))
        lines = []
        for ln in sdoc["lines"]:
            if rng.random() < 0.6 or (not lines and ln is sdoc["lines"][-1]):
                q = rng.randint(1, ln["qty"])
                gross = money(ln["price"] * q)
                disc = money(ln["disc"] * q / ln["qty"]) if ln["disc"] else Decimal("0.00")
                disc = min(disc, gross)
                lines.append(dict(ln, qty=q, gross=gross, disc=disc, amount=gross - disc))
        total = sum((l["amount"] for l in lines), Decimal("0.00"))
        if total <= 0:
            continue
        method = sdoc["payments"][0][0]
        method = method if method in ("cash", "card") else "card"
        docs.append(dict(kind="refund", ph=ph, when=when, cust=sdoc["cust"], lines=lines, total=total,
                         discount=sum((l["disc"] for l in lines), Decimal("0.00")), payments=[(method, total)],
                         rx=sdoc["rx"], parent=sdoc))

    # нумерация чеков по локальным датам и смены
    docs.sort(key=lambda d: d["when"])
    numbers = collections.defaultdict(int, start_numbers or {})
    shifts = {} if shifts_existing is None else shifts_existing
    rx_n = collections.defaultdict(int)
    for d in docs:
        local = d["when"].astimezone(MSK)
        ds = local.strftime("%Y%m%d")
        numbers[ds] += 1
        d["bk"] = f"RCPT-{ds}-{numbers[ds]:06d}"
        key = (d["ph"]["bk"], local.date())
        if key not in shifts:
            shifts[key] = dict(
                bk=f"SHF-{ds}-{d['ph']['num']:04d}-01", reg=f"REG-{d['ph']['num']:04d}-01",
                cashier=rng.choice(ref.cashiers[d["ph"]["bk"]])[0],
                opened=local_dt(local.date(), 8, 0), closed=local_dt(local.date(), 22, 0), new=True)
        d["shift"] = shifts[key]
        if d["rx"]:
            if d["parent"] is not None and d["parent"].get("rx_bk"):
                d["rx_bk"] = d["parent"]["rx_bk"]
            else:
                rx_n[local.year] += 1
                d["rx_bk"] = None   # назначается при записи (нужна сквозная нумерация по БД)
                d["rx_issued"] = d["when"] - dt.timedelta(hours=rng.randint(1, 120))
                d["doctor"] = rng.choice(D.DOCTORS)
    return docs, shifts


def split_payment(rng: random.Random, total: Decimal, has_card: bool):
    r = rng.random()
    if r < 0.40:
        return [("cash", total)]
    if r < 0.88 or total < Decimal("100"):
        return [("card", total)]
    a = money(total * Decimal(rng.uniform(0.2, 0.7)))
    a = min(max(a, Decimal("1.00")), total - Decimal("1.00"))
    second = rng.choice(["bonus", "cash", "certificate"] if has_card else ["cash", "certificate"])
    return [("card", total - a), (second, a)]


# ============================================================================
# Статистика (для --dry-run и отчёта)
# ============================================================================
def print_stats(ref, sim, docs):
    sales = [d for d in docs if d["kind"] == "sale"]
    refunds = [d for d in docs if d["kind"] == "refund"]
    lines = sum(len(d["lines"]) for d in docs)
    pays = sum(len(d["payments"]) for d in docs)
    print(f"  чеков: {len(docs)} (продаж {len(sales)}, возвратов {len(refunds)} = {100 * len(refunds) / len(docs):.2f}%)")
    print(f"  позиций: {lines}, оплат: {pays}")
    print(f"  партий: {len(sim.batches)}, строк закупок: {len(sim.po_lines)}, заказов: {len(sim.po_list)}")
    mt = collections.Counter(m["type"] for m in sim.moves)
    print(f"  движений: {len(sim.moves)} {dict(mt)}")
    stock = sum(len(v) for st in (sim.dc_stock, sim.ph_stock) for d in st.values() for v in d.values())
    print(f"  строк остатков (оценка): {stock + len(sim.batches)}")
    dow = collections.Counter(d["when"].astimezone(MSK).isoweekday() for d in sales)
    print(f"  сезонность max/min по дням недели: {max(dow.values()) / min(dow.values()):.2f}")
    rev = collections.Counter()
    for d in sales:
        for l in d["lines"]:
            rev[l["sku"]["bk"]] += float(l["amount"])
    vals = sorted(rev.values(), reverse=True)
    top = max(1, round(len(vals) * 0.1))
    print(f"  топ-10% SKU дают {100 * sum(vals[:top]) / sum(vals):.1f}% выручки (SKU с продажами: {len(vals)})")
    cnt = collections.Counter(d["ph"]["bk"] for d in docs)
    v = list(cnt.values())
    mean = sum(v) / len(v)
    cv = math.sqrt(sum((x - mean) ** 2 for x in v) / len(v)) / mean
    print(f"  коэффициент вариации по аптекам: {cv:.3f}")
    print(f"  доля покупок без карты: {100 * sum(1 for d in docs if d['cust'] is None) / len(docs):.1f}%")


# ============================================================================
# Запись в БД
# ============================================================================
def connect(db: str):
    host = os.environ.get("HW01_DB_HOST", "localhost")
    port = os.environ.get("HW01_DB_PORT", "5400")
    user = os.environ.get("HW01_DB_USER", "app")
    pw = os.environ.get("HW01_DB_PASSWORD", "app")
    conn = psycopg2.connect(host=host, port=port, user=user, password=pw, dbname=db, connect_timeout=10)
    conn.autocommit = False
    with conn.cursor() as cur:
        cur.execute("SET TimeZone = 'UTC'")
    return conn


def copy_rows(cur, table: str, cols, rows):
    rows = list(rows)
    if not rows:
        return 0
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    for r in rows:
        w.writerow(["" if v is None else (v.isoformat(sep=" ") if isinstance(v, dt.datetime) else v) for v in r])
    buf.seek(0)
    cur.copy_expert(f"COPY {table} ({', '.join(cols)}) FROM STDIN WITH (FORMAT csv)", buf)
    return len(rows)


def fetch_map(cur, sql):
    cur.execute(sql)
    return {r[0]: r[1] for r in cur.fetchall()}


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def load_catalog(conn, ref):
    cur = conn.cursor()
    copy_rows(cur, "catalog.manufacturer", ["manufacturer_bk", "name", "country", "created_at", "updated_at"],
              [(bk, n, c, midnight(ref.start - dt.timedelta(days=500)), midnight(ref.start - dt.timedelta(days=500)))
               for bk, n, c in ref.manufacturers])
    copy_rows(cur, "catalog.drug_form", ["name"], [(n,) for n, _ in ref.drug_forms])
    copy_rows(cur, "catalog.inn", ["inn_bk", "name"], ref.inns)
    mfr = fetch_map(cur, "SELECT manufacturer_bk, manufacturer_id FROM catalog.manufacturer")
    form = fetch_map(cur, "SELECT name, drug_form_id FROM catalog.drug_form")
    rows = []
    for s in ref.skus:
        cur_v = s["versions"][-1]
        rows.append((s["bk"], cur_v["name"], mfr[s["mfr"]], form.get(s["form"]), cur_v["category"], cur_v["is_rx"],
                     cur_v["is_vital"], s["pack"], True, cur_v["vf"], s["versions"][0]["vf"], cur_v["vf"]))
    copy_rows(cur, "catalog.sku", ["sku_bk", "sku_name", "manufacturer_id", "drug_form_id", "category", "is_rx",
                                   "is_vital", "pack_qty", "is_active", "attrs_valid_from", "created_at", "updated_at"], rows)
    sku_id = fetch_map(cur, "SELECT sku_bk, sku_id FROM catalog.sku")
    inn_id = fetch_map(cur, "SELECT inn_bk, inn_id FROM catalog.inn")
    copy_rows(cur, "catalog.sku_inn", ["sku_id", "inn_id", "created_at", "updated_at"],
              [(sku_id[s["bk"]], inn_id[ref.inns[i][0]], s["versions"][0]["vf"], s["versions"][0]["vf"])
               for s in ref.skus for i in s["inn_idx"]])
    hist = []
    for s in ref.skus:
        for a, b in zip(s["versions"], s["versions"][1:]):
            hist.append((sku_id[s["bk"]], a["vf"], b["vf"], a["name"], a["category"], a["is_rx"], a["is_vital"]))
    copy_rows(cur, "catalog.sku_history", ["sku_id", "valid_from", "valid_to", "sku_name", "category", "is_rx", "is_vital"], hist)
    # цены: сначала закрытые версии, затем открытые (иначе триггер закрытия может побеспокоить порядок)
    prices = sorted(ref.price_versions, key=lambda t: (t[5] is None, t[4]))
    copy_rows(cur, "catalog.price", ["sku_id", "price_scope", "scope_bk", "price", "currency", "valid_from", "valid_to",
                                     "is_current", "created_at", "updated_at"],
              [(sku_id[sku], scope, sbk, pr, "RUB", vf, vt, vt is None, vf, vf) for sku, scope, sbk, pr, vf, vt in prices])
    conn.commit()
    log(f"catalog: {len(ref.skus)} SKU, {len(prices)} версий цен")


def load_crm(conn, ref):
    cur = conn.cursor()
    cs = ref.customers
    copy_rows(cur, "crm.customer", ["customer_bk", "full_name", "birth_year", "sex", "city", "region", "loyalty_level",
                                    "registered_at", "created_at", "updated_at"],
              [(c["bk"], c["name"], c["birth_year"], c["sex"], c["city"], c["region"], c["level"], c["registered"],
                c["registered"], c["updated"]) for c in cs])
    cid = fetch_map(cur, "SELECT customer_bk, customer_id FROM crm.customer")
    contacts = []
    for c in cs:
        contacts.append((cid[c["bk"]], "phone", c["phone"], True, c["registered"], c["registered"]))
        if c["email"]:
            contacts.append((cid[c["bk"]], "email", c["email"], False, c["registered"], c["registered"]))
    copy_rows(cur, "crm.customer_contact", ["customer_id", "contact_type", "value", "is_primary", "created_at", "updated_at"], contacts)
    consents = []
    for c in cs:
        consents.append((cid[c["bk"]], "personal_data", c["registered"], None, c["registered"], c["registered"]))
        if c["marketing"]:
            g = c["registered"] + dt.timedelta(minutes=5)
            consents.append((cid[c["bk"]], "marketing", g, c["marketing_revoked"], g, c["marketing_revoked"] or g))
    copy_rows(cur, "crm.consent", ["customer_id", "consent_type", "granted_at", "revoked_at", "created_at", "updated_at"], consents)
    hist = [(cid[c["bk"]],) + c["history"] for c in cs if c["history"]]
    copy_rows(cur, "crm.customer_history", ["customer_id", "valid_from", "valid_to", "city", "region", "loyalty_level"], hist)
    cards, n = [], 0
    rng = random.Random("cards")
    for c in cs:
        if not c["has_card"]:
            continue
        issued = c["registered"] + dt.timedelta(days=rng.randint(0, 30))
        closed = issued + dt.timedelta(days=rng.randint(60, 400))
        if c["reissued"] and closed < midnight(ref.as_of):
            n += 1
            cards.append((f"CARD-{n:08d}", cid[c["bk"]], issued, closed, "closed", Decimal("0.00"), issued, closed))
            issued = closed + dt.timedelta(days=rng.randint(0, 5))
        n += 1
        status = "blocked" if rng.random() < 0.02 else "active"
        issued = min(issued, midnight(ref.as_of) - dt.timedelta(seconds=1))      # карта не выдаётся «в будущем»
        cards.append((f"CARD-{n:08d}", cid[c["bk"]], issued, None, status, money(rng.uniform(0, 4000)), issued, issued))
    copy_rows(cur, "crm.loyalty_card", ["card_bk", "customer_id", "issued_at", "closed_at", "status", "bonus_balance",
                                        "created_at", "updated_at"], cards)
    conn.commit()
    log(f"crm: {len(cs)} клиентов, {len(cards)} карт")


def load_wms(conn, ref, sim):
    cur = conn.cursor()
    t0 = midnight(ref.start - dt.timedelta(days=500))
    copy_rows(cur, "wms.supplier", ["supplier_bk", "supplier_name", "country", "created_at", "updated_at"],
              [(bk, n, c, t0, t0) for bk, n, c in ref.suppliers])
    copy_rows(cur, "wms.distribution_center", ["dc_bk", "dc_name", "region", "city", "created_at", "updated_at"],
              [(bk, n, r, c, t0, t0) for bk, n, r, c in ref.dcs])
    copy_rows(cur, "wms.batch", ["batch_bk", "sku_bk", "series_no", "manufactured_date", "expiry_date", "received_at",
                                 "created_at", "updated_at"],
              [(b.bk, b.sku, b.series, b.mfg, b.exp, b.received, b.received, b.received) for b in sim.batches])
    bid = fetch_map(cur, "SELECT batch_bk, batch_id FROM wms.batch")
    for b in sim.batches:
        b.db_id = bid[b.bk]
    sup = fetch_map(cur, "SELECT supplier_bk, supplier_id FROM wms.supplier")
    dcm = fetch_map(cur, "SELECT dc_bk, dc_id FROM wms.distribution_center")
    # заказы: BK = PO-<год>-<n>
    n_by_year = collections.defaultdict(int, sim.counters.get("po_year_n", {}))
    po_rows = []
    for po in sim.po_list:
        y = po["ordered_at"].year
        n_by_year[y] += 1
        po["bk"] = f"PO-{y}-{n_by_year[y]:06d}"
        po_rows.append((po["bk"], sup[po["supplier"]], dcm[po["dc"]], po["ordered_at"], po["ordered_at"], po["ordered_at"]))
    copy_rows(cur, "wms.purchase_order", ["purchase_bk", "supplier_id", "dc_id", "ordered_at", "created_at", "updated_at"], po_rows)
    pid = fetch_map(cur, "SELECT purchase_bk, purchase_id FROM wms.purchase_order")
    lines = []
    for ln in sim.po_lines:
        b = ln["batch"]
        lines.append((pid[ln["po"]["bk"]], ln["line_no"], ln["sku"], b.db_id if b else None, ln["qty"], ln["cost"], "RUB",
                      b.received if b else None, ln["po"]["ordered_at"], b.received if b else ln["po"]["ordered_at"]))
    copy_rows(cur, "wms.purchase_line", ["purchase_id", "line_no", "sku_bk", "batch_id", "quantity", "unit_cost", "currency",
                                         "received_at", "created_at", "updated_at"], lines)
    # движения строго в хронологическом порядке: триггер склада проверяет остаток на каждом шаге
    moves = sorted(sim.moves, key=lambda m: (m["moved_at"], m["seq"]))
    start_n = sim.counters.get("mov_n", 0)
    rows = []
    for i, m in enumerate(moves, start=start_n + 1):
        b = m["batch"]
        s_type, s_bk = m["src"]
        d_type, d_bk = m["dst"] if m["dst"] else (None, None)
        rows.append((f"MOV-{i:09d}", b.db_id, b.sku, m["type"], s_type, s_bk, d_type, d_bk, m["counterparty"],
                     m["qty"], m["dispatched_at"], m["moved_at"], m["moved_at"], m["moved_at"]))
    chunk = 20000
    for i in range(0, len(rows), chunk):
        copy_rows(cur, "wms.movement", ["movement_bk", "batch_id", "sku_bk", "movement_type", "src_type", "src_bk",
                                        "dst_type", "dst_bk", "counterparty_bk", "qty", "dispatched_at", "moved_at",
                                        "created_at", "updated_at"], rows[i:i + chunk])
    conn.commit()
    log(f"wms: {len(sim.batches)} партий, {len(lines)} строк закупок, {len(rows)} движений")


def load_pos_reference(conn, ref, sim):
    cur = conn.cursor()
    t0 = midnight(ref.start - dt.timedelta(days=500))
    copy_rows(cur, "pos.pharmacy", ["pharmacy_bk", "pharmacy_name", "region", "city", "address", "opened_at", "closed_at",
                                    "is_active", "created_at", "updated_at"],
              [(p["bk"], p["name"], p["region"], p["city"], p["address"], p["opened"], p["closed"], p["closed"] is None,
                t0, midnight(p["closed"]) if p["closed"] else t0) for p in ref.pharmacies])
    pid = fetch_map(cur, "SELECT pharmacy_bk, pharmacy_id FROM pos.pharmacy")
    copy_rows(cur, "pos.register", ["register_bk", "pharmacy_id", "created_at", "updated_at"],
              [(f"REG-{p['num']:04d}{suffix}", pid[p["bk"]], t0, t0) for p in ref.pharmacies for suffix in ("-01", "-02")])
    copy_rows(cur, "pos.cashier", ["cashier_bk", "full_name", "pharmacy_id", "created_at", "updated_at"],
              [(bk, name, pid[p["bk"]], t0, t0) for p in ref.pharmacies for bk, name in ref.cashiers[p["bk"]]])
    rx_since = midnight(ref.start - dt.timedelta(days=400))
    copy_rows(cur, "pos.sku_ref", ["sku_bk", "sku_name", "is_rx", "is_rx_since", "synced_at"],
              [(s["bk"], s["versions"][-1]["name"], s["is_rx"], rx_since if s["is_rx"] else None, midnight(ref.as_of))
               for s in ref.skus])
    copy_rows(cur, "pos.batch_ref", ["batch_bk", "sku_bk", "expiry_date", "synced_at"],
              [(b.bk, b.sku, b.exp, b.received) for b in sim.batches])
    conn.commit()
    log(f"pos: справочники ({len(ref.pharmacies)} аптек, {len(ref.skus)} sku_ref, {len(sim.batches)} batch_ref)")


def load_pos_facts(conn, ref, docs, shifts):
    cur = conn.cursor()
    pid = fetch_map(cur, "SELECT pharmacy_bk, pharmacy_id FROM pos.pharmacy")
    reg = fetch_map(cur, "SELECT register_bk, register_id FROM pos.register")
    csh = fetch_map(cur, "SELECT cashier_bk, cashier_id FROM pos.cashier")
    # смены
    new_shifts = [(k, s) for k, s in shifts.items() if s.get("new")]
    copy_rows(cur, "pos.shift", ["shift_bk", "register_id", "cashier_id", "opened_at", "closed_at", "created_at", "updated_at"],
              [(s["bk"], reg[s["reg"]], csh[s["cashier"]], s["opened"], s["closed"], s["opened"], s["closed"])
               for _, s in new_shifts])
    sid = fetch_map(cur, "SELECT shift_bk, shift_id FROM pos.shift")
    # рецепты
    cur.execute("SELECT prescription_bk FROM pos.prescription")
    rx_counters = collections.defaultdict(int)
    for (bk,) in cur.fetchall():
        y, n = int(bk[3:7]), int(bk[8:])
        rx_counters[y] = max(rx_counters[y], n)
    rx_rows = []
    for d in docs:
        if d["rx"] and d["kind"] == "sale":
            y = d["when"].astimezone(MSK).year
            rx_counters[y] += 1
            d["rx_bk"] = f"RX-{y}-{rx_counters[y]:07d}"
            rx_rows.append((d["rx_bk"], d["cust"]["bk"] if d["cust"] else None, d["rx_issued"], d["doctor"], d["rx_issued"], d["rx_issued"]))
    for d in docs:
        if d["kind"] == "refund" and d["rx"]:
            d["rx_bk"] = d["parent"]["rx_bk"]
    copy_rows(cur, "pos.prescription", ["prescription_bk", "customer_bk", "issued_at", "doctor_name", "created_at", "updated_at"], rx_rows)
    rxid = fetch_map(cur, "SELECT prescription_bk, prescription_id FROM pos.prescription")
    cols = ["receipt_bk", "pharmacy_id", "shift_id", "customer_bk", "receipt_dt", "doc_type", "parent_receipt_id",
            "total_amount", "discount_amount", "currency", "prescription_id", "created_at", "updated_at"]

    def row(d, parent_id=None):
        return (d["bk"], pid[d["ph"]["bk"]], sid[d["shift"]["bk"]], d["cust"]["bk"] if d["cust"] else None, d["when"],
                d["kind"], parent_id, d["total"], d["discount"], "RUB", rxid.get(d.get("rx_bk")) if d["rx"] else None,
                d["when"], d["when"])
    sales = [d for d in docs if d["kind"] == "sale"]
    refunds = [d for d in docs if d["kind"] == "refund"]
    copy_rows(cur, "pos.receipt", cols, [row(d) for d in sales])
    rid = fetch_map(cur, "SELECT receipt_bk, receipt_id FROM pos.receipt")
    copy_rows(cur, "pos.receipt", cols, [row(d, rid[d["parent"]["bk"]]) for d in refunds])
    rid = fetch_map(cur, "SELECT receipt_bk, receipt_id FROM pos.receipt")
    lcols = ["receipt_id", "line_no", "sku_bk", "batch_bk", "quantity", "unit_price", "line_discount", "is_rx_snapshot",
             "sku_name_snapshot", "created_at", "updated_at"]
    lrows = []
    for d in sales + refunds:
        for n, l in enumerate(d["lines"], start=1):
            lrows.append((rid[d["bk"]], n, l["sku"]["bk"], l["batch"].bk, l["qty"], l["price"], l["disc"], l["rx"], l["name"],
                          d["when"], d["when"]))
    for i in range(0, len(lrows), 30000):
        copy_rows(cur, "pos.receipt_line", lcols, lrows[i:i + 30000])
    prows = [(rid[d["bk"]], n, m, a, "RUB", d["when"], d["when"]) for d in sales + refunds
             for n, (m, a) in enumerate(d["payments"], start=1)]
    copy_rows(cur, "pos.payment", ["receipt_id", "payment_no", "payment_method", "amount", "currency", "created_at", "updated_at"], prows)
    conn.commit()        # здесь же срабатывают отложенные триггеры согласованности (I3, I10, I12)
    log(f"pos: {len(docs)} чеков, {len(lrows)} позиций, {len(prows)} оплат")
    return len(docs)


# ============================================================================
# Догенерация поверх существующих данных
# ============================================================================
def load_state(ref_seed: int, as_of: dt.date):
    """Читает существующий «мир» из БД: справочники, остатки, цены, клиентов."""
    ref = Ref()
    ref.as_of = as_of
    ref.start = as_of - dt.timedelta(days=HISTORY_DAYS)
    ref.dcs = list(D.DCS)
    ref.regions = D.REGIONS
    ref.suppliers = []
    conns = {n: connect(f"{n}_service_db") for n in ("crm", "catalog", "pos", "wms")}
    try:
        # цены
        cur = conns["catalog"].cursor()
        cur.execute("SELECT sku, price_scope, scope_bk, price, valid_from, valid_to FROM contract.v_price")
        ref.price_versions = [(a, b, c, d, e, f) for a, b, c, d, e, f in cur.fetchall()]
        build_price_index(ref)
        # sku + популярность из истории продаж
        cur = conns["pos"].cursor()
        cur.execute("""SELECT r.sku_bk, r.sku_name, r.is_rx, r.is_rx_since, coalesce(c.n, 0) + 1
                         FROM pos.sku_ref r LEFT JOIN (SELECT sku_bk, count(*) n FROM pos.receipt_line GROUP BY 1) c USING (sku_bk)
                        ORDER BY r.sku_bk""")
        rows = cur.fetchall()
        tot = float(sum(r[4] for r in rows))
        ref.skus = []
        for bk, name, is_rx, since, n in rows:
            ref.skus.append(dict(bk=bk, name=name, is_rx=is_rx, is_vital=False, weight=n / tot,
                                 versions=[dict(vf=midnight(ref.start - dt.timedelta(days=400)), name=name, category=None,
                                                is_rx=is_rx, is_vital=False)], category=None))
        ref.sku_by_bk = {s["bk"]: s for s in ref.skus}
        cur.execute("""SELECT pharmacy_bk, pharmacy_name, region, city, address, opened_at, closed_at
                         FROM pos.pharmacy ORDER BY pharmacy_bk""")
        ref.pharmacies = []
        for bk, name, region, city, address, opened, closed in cur.fetchall():
            ref.pharmacies.append(dict(bk=bk, num=int(bk[3:]), name=name, region=region, city=city, address=address,
                                       opened=opened, closed=closed, dc=next(r[3] for r in D.REGIONS if r[0] == region),
                                       weight=1.0))
        cur.execute("SELECT count(*), pharmacy_bk FROM pos.receipt r JOIN pos.pharmacy p USING (pharmacy_id) GROUP BY 2")
        cnt = {bk: n for n, bk in cur.fetchall()}
        for p in ref.pharmacies:
            p["weight"] = float(cnt.get(p["bk"], 0)) + 1.0
        cur.execute("""SELECT c.cashier_bk, c.full_name, p.pharmacy_bk FROM pos.cashier c JOIN pos.pharmacy p USING (pharmacy_id)
                        ORDER BY 1""")
        ref.cashiers = collections.defaultdict(list)
        for bk, name, ph in cur.fetchall():
            ref.cashiers[ph].append((bk, name))
        cur.execute("""SELECT s.shift_bk, p.pharmacy_bk, s.opened_at FROM pos.shift s
                         JOIN pos.register r USING (register_id) JOIN pos.pharmacy p USING (pharmacy_id)""")
        shifts = {}
        for bk, ph, opened in cur.fetchall():
            shifts[(ph, opened.astimezone(MSK).date())] = dict(bk=bk)
        # номера чеков по дням
        cur.execute("SELECT substr(receipt_bk, 6, 8), max(substr(receipt_bk, 15)::int) FROM pos.receipt GROUP BY 1")
        numbers = {d: n for d, n in cur.fetchall()}
        # клиенты с активной картой
        cur = conns["crm"].cursor()
        cur.execute("""SELECT c.customer_bk, c.loyalty_level, c.registered_at FROM contract.v_customer c
                         JOIN contract.v_loyalty_card l USING (customer_bk) WHERE l.status = 'active' ORDER BY 1""")
        pool = [dict(bk=bk, level=lvl or "basic", registered=reg) for bk, lvl, reg in cur.fetchall()]
        # склад: партии и остатки
        cur = conns["wms"].cursor()
        cur.execute("SELECT batch_id, batch_bk, sku_bk, series_no, manufactured_date, expiry_date, received_at FROM wms.batch ORDER BY batch_id")
        batches = {}
        sim_batches = []
        for bid, bk, sku, series, mfg, exp, rec in cur.fetchall():
            b = Batch(idx=len(sim_batches), bk=bk, sku=sku, series=series, mfg=mfg, exp=exp, received=rec,
                      supplier=None, qty0=0, db_id=bid)
            batches[bid] = b
            sim_batches.append(b)
        cur.execute("SELECT location_type, location_bk, batch_id, qty_on_hand FROM wms.stock WHERE qty_on_hand > 0 ORDER BY 1, 2, 3")
        stock = [(t, bk, batches[bid], int(q)) for t, bk, bid, q in cur.fetchall()]
        cur.execute("SELECT coalesce(max(substr(movement_bk, 5)::int), 0) FROM wms.movement")
        mov_n = cur.fetchone()[0]
        cur.execute("SELECT substr(batch_bk, 7, 4)::int, max(substr(batch_bk, 12)::int) FROM wms.batch GROUP BY 1")
        batch_year_n = {y: n for y, n in cur.fetchall()}
        cur.execute("SELECT substr(purchase_bk, 4, 4)::int, max(substr(purchase_bk, 9)::int) FROM wms.purchase_order GROUP BY 1")
        po_year_n = {y: n for y, n in cur.fetchall()}
    finally:
        for c in conns.values():
            c.rollback()
            c.close()
    counters = dict(mov_n=mov_n, batch_year_n=batch_year_n, po_year_n=po_year_n)
    return ref, shifts, numbers, pool, sim_batches, stock, counters


def run_delta(seed: int, as_of: dt.date, n_receipts: int, dry: bool):
    log(f"догенерация: seed={seed}, чеков={n_receipts}")
    ref, shifts, numbers, pool, batches, stock, counters = load_state(seed, as_of)
    rng = random.Random(f"{seed}:delta:{sum(numbers.values())}")
    ref.lam = None
    sim = Sim.__new__(Sim)
    sim.ref, sim.rng, sim.as_of = ref, rng, as_of
    sim.batches = batches
    sim.dc_stock = collections.defaultdict(lambda: collections.defaultdict(list))
    sim.ph_stock = collections.defaultdict(lambda: collections.defaultdict(list))
    sim.dc_avail = collections.defaultdict(int)
    sim.arrivals, sim.expiring, sim.moves, sim.po_list, sim.po_lines = [], collections.defaultdict(list), [], [], []
    sim.batch_locs = collections.defaultdict(set)
    sim.pending_sales = collections.defaultdict(int)
    sim.sales, sim.seq, sim.counters = [], 0, counters
    sim.batch_year_n = collections.defaultdict(int, counters["batch_year_n"])
    sim.series_used = set()
    cum, acc = [], 0.0
    for s in ref.skus:
        acc += s["weight"]
        cum.append(acc)
    sim.sku_cum, sim.sku_total = cum, acc
    for ltype, lbk, b, q in stock:
        target = sim.dc_stock[lbk][b.sku] if ltype == "dc" else sim.ph_stock[lbk][b.sku]
        Sim._insert_fefo(target, b, q)
        sim.batch_locs[b.idx].add((ltype, lbk))
    # продажи в последние дни: пока не наберём нужное число чеков
    days = [as_of - dt.timedelta(days=i) for i in range(6, -1, -1)]
    raw = [WEEKDAY[d.isoweekday()] for d in days]
    per_unit = n_receipts / (1 + REFUND_RATE) / sum(raw)
    guard = 0
    while len(sim.sales) < n_receipts / (1 + REFUND_RATE) and guard < 20:
        guard += 1
        for d, r in zip(days, raw):
            need = n_receipts / (1 + REFUND_RATE) - len(sim.sales)
            if need <= 0:
                break
            sim.sell_day(d, max(1, min(int(round(per_unit * r * (1.2 if guard > 1 else 1.0))), int(need) + 1)))
    sim.flush_sales(as_of, final=True)
    docs, shifts = finalize_receipts(ref, rng, sim, start_numbers=numbers, shifts_existing=shifts, customers_pool=pool)
    log(f"сформировано {len(docs)} документов, {len(sim.moves)} движений")
    if dry:
        return
    # смены, которых ещё нет в БД, помечены new=True; существующие — нет
    wconn, pconn = connect("wms_service_db"), connect("pos_service_db")
    try:
        load_wms_delta(wconn, sim)
        # sku/batch refs уже есть; новых партий в догенерации нет
        n = load_pos_facts(pconn, ref, docs, shifts)
        with pconn.cursor() as cur:
            cur.execute("INSERT INTO pos.seed_log (seed, kind, receipts) VALUES (%s, 'delta', %s)", (seed, n))
        pconn.commit()
    finally:
        wconn.close()
        pconn.close()


def load_wms_delta(conn, sim):
    cur = conn.cursor()
    moves = sorted(sim.moves, key=lambda m: (m["moved_at"], m["seq"]))
    start_n = sim.counters.get("mov_n", 0)
    rows = []
    for i, m in enumerate(moves, start=start_n + 1):
        b = m["batch"]
        s_type, s_bk = m["src"]
        d_type, d_bk = m["dst"] if m["dst"] else (None, None)
        rows.append((f"MOV-{i:09d}", b.db_id, b.sku, m["type"], s_type, s_bk, d_type, d_bk, m["counterparty"],
                     m["qty"], m["dispatched_at"], m["moved_at"], m["moved_at"], m["moved_at"]))
    copy_rows(cur, "wms.movement", ["movement_bk", "batch_id", "sku_bk", "movement_type", "src_type", "src_bk",
                                    "dst_type", "dst_bk", "counterparty_bk", "qty", "dispatched_at", "moved_at",
                                    "created_at", "updated_at"], rows)
    conn.commit()
    log(f"wms: добавлено {len(rows)} движений")


# ============================================================================
# main
# ============================================================================
def main():
    ap = argparse.ArgumentParser(description="генератор данных ДЗ №1")
    ap.add_argument("--seed", type=int, required=True)
    ap.add_argument("--receipts", type=int, default=None, help="догенерировать N чеков поверх существующих данных")
    ap.add_argument("--as-of", default=AS_OF_DEFAULT.isoformat(), help="конец истории (YYYY-MM-DD)")
    ap.add_argument("--scale", type=float, default=1.0, help="множитель объёма базовой загрузки")
    ap.add_argument("--dry-run", action="store_true", help="не писать в БД, показать статистику")
    args = ap.parse_args()
    as_of = dt.date.fromisoformat(args.as_of)
    t0 = time.time()

    if args.receipts is not None:
        run_delta(args.seed, as_of, args.receipts, args.dry_run)
        log(f"готово за {time.time() - t0:.0f} с")
        return 0

    if not args.dry_run:
        conn = connect("pos_service_db")
        with conn.cursor() as cur:
            cur.execute("SELECT seed FROM pos.seed_log WHERE kind = 'base' ORDER BY ran_at LIMIT 1")
            row = cur.fetchone()
        conn.close()
        if row is not None:
            print(f"данные уже загружены (базовый seed={row[0]}): повторный запуск с тем же seed ничего не меняет")
            return 0
        probes = (("crm_service_db", "crm.customer"), ("catalog_service_db", "catalog.sku"),
                  ("wms_service_db", "wms.batch"), ("pos_service_db", "pos.pharmacy"))
        dirty = []
        for db, table in probes:
            c = connect(db)
            with c.cursor() as cur:
                cur.execute(f"SELECT EXISTS (SELECT 1 FROM {table})")
                if cur.fetchone()[0]:
                    dirty.append(table)
            c.close()
        if dirty:
            print("ОШИБКА: найдена частичная загрузка без записи в pos.seed_log (" + ", ".join(dirty) + ").\n"
                  "Пересоздайте стенд: docker compose down -v && docker compose up -d --build", file=sys.stderr)
            return 2

    log(f"базовая загрузка: seed={args.seed}, конец истории {as_of}")
    ref = gen_reference(args.seed, as_of)
    rng = random.Random(f"{args.seed}:world")
    sim = Sim(ref, rng, as_of)
    sim.run_history(ref.start, args.scale)
    docs, shifts = finalize_receipts(ref, random.Random(f"{args.seed}:docs"), sim)
    log("мир сгенерирован (в памяти)")
    print_stats(ref, sim, docs)
    if args.dry_run:
        log(f"dry-run: готово за {time.time() - t0:.0f} с")
        return 0

    conns = {n: connect(f"{n}_service_db") for n in ("crm", "catalog", "pos", "wms")}
    try:
        load_catalog(conns["catalog"], ref)
        load_crm(conns["crm"], ref)
        load_wms(conns["wms"], ref, sim)
        load_pos_reference(conns["pos"], ref, sim)
        n = load_pos_facts(conns["pos"], ref, docs, shifts)
        with conns["pos"].cursor() as cur:
            cur.execute("INSERT INTO pos.seed_log (seed, kind, receipts) VALUES (%s, 'base', %s)", (args.seed, n))
        conns["pos"].commit()
    finally:
        for c in conns.values():
            c.close()
    log(f"готово за {time.time() - t0:.0f} с")
    return 0


if __name__ == "__main__":
    sys.exit(main())
