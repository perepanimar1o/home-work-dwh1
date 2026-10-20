# ER-диаграммы

Диаграммы четырёх сервисов (mermaid) **сгенерированы из системных каталогов работающих баз**: `python scripts/gen_erd.py` (стенд должен быть поднят), поэтому они всегда соответствуют фактической схеме.
На каждой диаграмме видны ключи (`PK`, `FK`, `UK`), типы и обязательность (`NOT NULL` / `NULL`); связи между таблицами — только внутри сервиса. Под диаграммой — интерфейс `contract` и таблица **логических ссылок по business key** на другие сервисы
(кросс-сервисных FOREIGN KEY нет и быть не может: это разные базы).

| сервис | диаграмма |
|--------|-----------|
| CRM | [crm_service.md](crm_service.md) |
| Каталог | [catalog_service.md](catalog_service.md) |
| POS | [pos_service.md](pos_service.md) |
| WMS | [wms_service.md](wms_service.md) |

## Связи между сервисами (по business key)

```mermaid
flowchart LR
    CRM["CRM<br/>customer_bk, card_bk"]
    CAT["Каталог<br/>sku, цены"]
    POS["POS<br/>pharmacy_bk, receipt_bk"]
    WMS["WMS<br/>batch_bk, dc_bk, supplier_bk"]

    POS -- "receipt.customer_bk (NULL = без карты)" --> CRM
    POS -- "receipt_line.sku_bk" --> CAT
    POS -- "receipt_line.batch_bk" --> WMS
    WMS -- "batch.sku_bk, purchase_line.sku_bk, movement.sku_bk" --> CAT
    WMS -- "movement/stock: src_bk, dst_bk, location_bk (полиморфно: dc | pharmacy)" --> POS
    CAT -- "price.scope_bk (pharmacy | region | ALL)" --> POS
```

Стрелка значит «хранит значение business key сущности, которой владеет другой сервис». Ссылки не проверяются СУБД (разные базы) — целостность держит приложение/генератор и проверяют кросс-проверки чекера X01–X10;
для двух самых важных ограничений (I8, I10) нужные поля чужих справочников хранятся в POS локально (`pos.sku_ref`, `pos.batch_ref`, ADR-0004).
