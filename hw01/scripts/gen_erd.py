#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Генератор ER-диаграмм (mermaid) из системных каталогов живых баз: docs/erd/<сервис>.md.
Диаграммы строятся по ФАКТИЧЕСКОЙ схеме, поэтому не расходятся с миграциями.

    python scripts/gen_erd.py        # стенд должен быть поднят (подключение суперпользователем к лидеру :5400)
"""
import os
import re
import sys

import psycopg2

HOST = os.environ.get("HW01_DB_HOST", "localhost")
PORT = os.environ.get("HW01_ADMIN_PORT", "5400")
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "docs", "erd")

SERVICES = {
    "crm": ("crm_service_db", "crm", "Клиенты, контакты, согласия, карты лояльности"),
    "catalog": ("catalog_service_db", "catalog", "Номенклатура, МНН, производители, цены с историей"),
    "pos": ("pos_service_db", "pos", "Аптеки, кассы, смены, кассовые документы, оплаты"),
    "wms": ("wms_service_db", "wms", "Поставщики, распредцентры, закупки, партии, движения, остатки"),
}

# Логические (кросс-сервисные) ссылки по business key — FOREIGN KEY между базами запрещён.
CROSS = {
    "pos": [
        ("receipt.customer_bk", "crm", "customer.customer_bk", "покупатель; NULL = покупка без карты"),
        ("prescription.customer_bk", "crm", "customer.customer_bk", "пациент по рецепту"),
        ("receipt_line.sku_bk", "catalog", "sku.sku_bk", "позиция каталога (+ локальная копия sku_ref)"),
        ("receipt_line.batch_bk", "wms", "batch.batch_bk", "серия, из которой продано (+ локальная копия batch_ref)"),
        ("sku_ref.sku_bk", "catalog", "sku.sku_bk", "копия справочника для проверки I8/I10"),
        ("batch_ref.batch_bk", "wms", "batch.batch_bk", "копия справочника для проверки I8"),
        ("batch_ref.sku_bk", "catalog", "sku.sku_bk", ""),
    ],
    "wms": [
        ("batch.sku_bk", "catalog", "sku.sku_bk", "товарная позиция партии"),
        ("purchase_line.sku_bk", "catalog", "sku.sku_bk", "закупаемая позиция"),
        ("movement.sku_bk", "catalog", "sku.sku_bk", "(вместе с batch_id — составной FK внутри сервиса)"),
        ("movement.src_bk / dst_bk", "pos", "pharmacy.pharmacy_bk", "если src_type/dst_type = 'pharmacy' (полиморфная ссылка)"),
        ("stock.location_bk", "pos", "pharmacy.pharmacy_bk", "если location_type = 'pharmacy' (полиморфная ссылка)"),
    ],
    "catalog": [
        ("price.scope_bk", "pos", "pharmacy.pharmacy_bk", "если price_scope = 'pharmacy'; для 'region' — название региона из pos.pharmacy.region; для 'chain' — 'ALL'"),
    ],
    "crm": [],
}


def mtype(t: str) -> str:
    t = t.lower()
    t = {"timestamp with time zone": "timestamptz", "character varying": "varchar", "character": "char",
         "double precision": "float8"}.get(re.sub(r"\(.*\)", "", t).strip(), t)
    return re.sub(r"[^a-z0-9_]", "_", t).strip("_") if "(" not in t else re.sub(r"[^a-z0-9_]", "_", t).strip("_")


def entity(cur, schema):
    cur.execute("""
        SELECT c.relname, a.attnum, a.attname, format_type(a.atttypid, a.atttypmod), a.attnotnull,
               pg_get_expr(d.adbin, d.adrelid), a.attgenerated, a.attidentity
          FROM pg_class c
          JOIN pg_namespace n ON n.oid = c.relnamespace
          JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
          LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
         WHERE n.nspname = %s AND c.relkind = 'r'
         ORDER BY c.relname, a.attnum""", (schema,))
    cols = {}
    for rel, num, name, typ, notnull, default, gen, ident in cur.fetchall():
        cols.setdefault(rel, []).append(dict(name=name, type=typ, notnull=notnull, default=default, gen=gen, ident=ident))
    cur.execute("""
        SELECT c.relname, k.contype, k.conname, k.conkey, k.confrelid::regclass::text, k.confkey,
               (SELECT array_agg(a.attname ORDER BY x.ord) FROM unnest(k.conkey) WITH ORDINALITY x(num, ord)
                  JOIN pg_attribute a ON a.attrelid = k.conrelid AND a.attnum = x.num),
               (SELECT array_agg(a.attname ORDER BY x.ord) FROM unnest(k.confkey) WITH ORDINALITY x(num, ord)
                  JOIN pg_attribute a ON a.attrelid = k.confrelid AND a.attnum = x.num)
          FROM pg_constraint k
          JOIN pg_class c ON c.oid = k.conrelid
          JOIN pg_namespace n ON n.oid = c.relnamespace
         WHERE n.nspname = %s AND k.contype IN ('p', 'u', 'f')""", (schema,))
    cons = cur.fetchall()
    return cols, cons


def render(key, db, schema, title):
    conn = psycopg2.connect(host=HOST, port=PORT, user="postgres", password="postgres", dbname=db, connect_timeout=10)
    cur = conn.cursor()
    cols, cons = entity(cur, schema)
    pk, uk, fk_cols, rels = {}, {}, {}, []
    for rel, kind, name, _, ref, _, kcols, rcols in cons:
        if kind == "p":
            pk[rel] = kcols
        elif kind == "u":
            for c in kcols if len(kcols) == 1 else []:
                uk.setdefault(rel, set()).add(c)
        else:
            parent = ref.split(".")[-1]
            for c in kcols:
                fk_cols.setdefault(rel, set()).add(c)
            rels.append((rel, kcols, parent, rcols))
    lines = [f"# ER-диаграмма: {key}_service_db", "",
             f"{title}. Схема `{schema}` (базовые таблицы) и интерфейс `contract` (представления для DWH).", "",
             "Диаграмма **сгенерирована** из системных каталогов работающей базы (`python scripts/gen_erd.py`), поэтому отражает фактическую схему.",
             "Обозначения: `PK` — первичный ключ, `FK` — внешний ключ (только внутри сервиса), `UK` — уникальный ключ (в т.ч. business key). "
             "В комментарии: `NOT NULL` / `NULL` — обязательность, `GENERATED` — вычисляемая колонка, `IDENTITY` — суррогатный ключ.", "",
             "```mermaid", "erDiagram"]
    for rel in sorted(cols):
        lines.append(f"    {rel} {{")
        for c in cols[rel]:
            flags = []
            if c["name"] in (pk.get(rel) or []):
                flags.append("PK")
            if c["name"] in fk_cols.get(rel, set()):
                flags.append("FK")
            if c["name"] in uk.get(rel, set()):
                flags.append("UK")
            notes = ["NOT NULL" if c["notnull"] else "NULL"]
            if c["gen"]:
                notes.append("GENERATED")
            if c["ident"]:
                notes.append("IDENTITY")
            elif c["default"] and not c["gen"]:
                notes.append("DEFAULT " + re.sub(r"[\"'()]", "", c["default"].split("::")[0])[:24])
            lines.append(f"        {mtype(c['type'])} {c['name']}{(' ' + ', '.join(flags)) if flags else ''} \"{'; '.join(notes)}\"")
        lines.append("    }")
    for rel, kcols, parent, rcols in sorted(rels):
        nullable = any(not next(x for x in cols[rel] if x["name"] == c)["notnull"] for c in kcols)
        one_to_one = set(kcols) == set(pk.get(rel) or []) or (len(kcols) == 1 and kcols[0] in uk.get(rel, set()))
        left = "o|" if False else "||"
        right = ("o|" if one_to_one else "o{") if not nullable else "o{"
        lines.append(f"    {parent} {left}--{right} {rel} : \"{','.join(kcols)}\"")
    lines += ["```", ""]

    cur.execute("""SELECT c.relname, string_agg(a.attname || ' ' || format_type(a.atttypid, a.atttypmod), ', ' ORDER BY a.attnum)
                     FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace
                     JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
                    WHERE n.nspname = 'contract' AND c.relkind = 'v' GROUP BY c.relname ORDER BY 1""")
    lines += ["## Интерфейс `contract` (представления)", "", "| представление | колонки (имя тип) |", "|---|---|"]
    for name, cl in cur.fetchall():
        lines.append(f"| `contract.{name}` | {cl.replace('timestamp with time zone', 'timestamptz')} |")
    lines.append("")
    if CROSS.get(key):
        lines += ["## Логические ссылки на другие сервисы (по business key, **без FOREIGN KEY**)", "",
                  "| колонка этого сервиса | ссылается на | комментарий |", "|---|---|---|"]
        for col, osvc, ocol, note in CROSS[key]:
            lines.append(f"| `{col}` | `{osvc}_service_db`: `{ocol}` | {note} |")
        lines.append("")
    cur.execute("""SELECT t.tgname, c.relname, pg_get_triggerdef(t.oid)
                     FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND NOT t.tgisinternal ORDER BY c.relname, t.tgname""", (schema,))
    trg = cur.fetchall()
    cur.execute("""SELECT c.relname, k.conname, pg_get_constraintdef(k.oid)
                     FROM pg_constraint k JOIN pg_class c ON c.oid = k.conrelid JOIN pg_namespace n ON n.oid = c.relnamespace
                    WHERE n.nspname = %s AND k.contype IN ('c', 'x') ORDER BY c.relname, k.conname""", (schema,))
    chk = cur.fetchall()
    lines += ["## Защиты схемы (CHECK / EXCLUDE и триггеры)", "",
              f"CHECK/EXCLUDE: **{len(chk)}**, триггеров: **{len(trg)}** (полный список — в `migrations/{key}/`).", ""]
    for rel, name, definition in chk:
        if definition.startswith(("EXCLUDE", "CHECK ((" )) and ("EXCLUDE" in definition or len(definition) < 150):
            lines.append(f"- `{rel}.{name}`: `{definition}`")
    lines.append("")
    conn.close()
    path = os.path.join(OUT, f"{key}_service.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    print(f"{path}: {len(cols)} таблиц, {len(rels)} внутренних FK, {len(chk)} CHECK/EXCLUDE, {len(trg)} триггеров")


def main():
    os.makedirs(OUT, exist_ok=True)
    for key, (db, schema, title) in SERVICES.items():
        render(key, db, schema, title)
    return 0


if __name__ == "__main__":
    sys.exit(main())
