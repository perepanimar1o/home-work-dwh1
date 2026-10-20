# ER-диаграмма: wms_service_db

Поставщики, распредцентры, закупки, партии, движения, остатки. Схема `wms` (базовые таблицы) и интерфейс `contract` (представления для DWH).

Диаграмма **сгенерирована** из системных каталогов работающей базы (`python scripts/gen_erd.py`), поэтому отражает фактическую схему.
Обозначения: `PK` — первичный ключ, `FK` — внешний ключ (только внутри сервиса), `UK` — уникальный ключ (в т.ч. business key). В комментарии: `NOT NULL` / `NULL` — обязательность, `GENERATED` — вычисляемая колонка, `IDENTITY` — суррогатный ключ.

```mermaid
erDiagram
    batch {
        bigint batch_id PK "NOT NULL; IDENTITY"
        text batch_bk UK "NOT NULL"
        text sku_bk "NOT NULL"
        text series_no "NOT NULL"
        date manufactured_date "NOT NULL"
        date expiry_date "NOT NULL"
        timestamptz received_at "NOT NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
        text source_system "NOT NULL; DEFAULT wms_service"
    }
    distribution_center {
        bigint dc_id PK "NOT NULL; IDENTITY"
        text dc_bk UK "NOT NULL"
        text dc_name "NOT NULL"
        text region "NOT NULL"
        text city "NOT NULL"
        boolean is_active "NOT NULL; DEFAULT true"
        timestamptz deleted_at "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
        text source_system "NOT NULL; DEFAULT wms_service"
    }
    movement {
        bigint movement_id PK "NOT NULL; IDENTITY"
        text movement_bk UK "NOT NULL"
        bigint batch_id FK "NOT NULL"
        text sku_bk FK "NOT NULL"
        text movement_type "NOT NULL"
        text src_type "NULL"
        text src_bk "NULL"
        text dst_type "NULL"
        text dst_bk "NULL"
        text counterparty_bk "NULL"
        numeric_12_3 qty "NOT NULL"
        timestamptz dispatched_at "NULL"
        timestamptz moved_at "NOT NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
        text source_system "NOT NULL; DEFAULT wms_service"
    }
    purchase_line {
        bigint purchase_id PK, FK "NOT NULL"
        integer line_no PK "NOT NULL"
        text sku_bk "NOT NULL"
        bigint batch_id FK "NULL"
        numeric_12_3 quantity "NOT NULL"
        numeric_12_2 unit_cost "NOT NULL"
        numeric_14_2 line_cost "NULL; GENERATED"
        char currency "NOT NULL; DEFAULT RUB"
        timestamptz received_at "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    purchase_order {
        bigint purchase_id PK "NOT NULL; IDENTITY"
        text purchase_bk UK "NOT NULL"
        bigint supplier_id FK "NOT NULL"
        bigint dc_id FK "NOT NULL"
        timestamptz ordered_at "NOT NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    stock {
        text location_type PK "NOT NULL"
        text location_bk PK "NOT NULL"
        bigint batch_id PK, FK "NOT NULL"
        numeric_12_3 qty_on_hand "NOT NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    supplier {
        bigint supplier_id PK "NOT NULL; IDENTITY"
        text supplier_bk UK "NOT NULL"
        text supplier_name "NOT NULL"
        text country "NOT NULL"
        boolean is_active "NOT NULL; DEFAULT true"
        timestamptz deleted_at "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
        text source_system "NOT NULL; DEFAULT wms_service"
    }
    batch ||--o{ movement : "batch_id,sku_bk"
    batch ||--o{ purchase_line : "batch_id"
    purchase_order ||--o{ purchase_line : "purchase_id"
    distribution_center ||--o{ purchase_order : "dc_id"
    supplier ||--o{ purchase_order : "supplier_id"
    batch ||--o{ stock : "batch_id"
```

## Интерфейс `contract` (представления)

| представление | колонки (имя тип) |
|---|---|
| `contract.v_batch` | batch_bk text, sku text, series_no text, manufactured_date date, expiry_date date, received_at timestamptz, updated_at timestamptz |
| `contract.v_distribution_center` | dc_bk text, dc_name text, region text, city text, is_active boolean, updated_at timestamptz |
| `contract.v_movement` | movement_bk text, batch_bk text, sku text, movement_type text, src_type text, src_bk text, dst_type text, dst_bk text, qty numeric(12,3), dispatched_at timestamptz, moved_at timestamptz, updated_at timestamptz |
| `contract.v_purchase_line` | purchase_bk text, line_no integer, supplier_bk text, dc_bk text, sku text, batch_bk text, ordered_at timestamptz, received_at timestamptz, quantity numeric(12,3), unit_cost numeric(12,2), line_cost numeric(14,2), currency text, updated_at timestamptz |
| `contract.v_stock` | location_type text, location_bk text, sku text, batch_bk text, qty_on_hand numeric(12,3), updated_at timestamptz |
| `contract.v_supplier` | supplier_bk text, supplier_name text, country text, is_active boolean, updated_at timestamptz |

## Логические ссылки на другие сервисы (по business key, **без FOREIGN KEY**)

| колонка этого сервиса | ссылается на | комментарий |
|---|---|---|
| `batch.sku_bk` | `catalog_service_db`: `sku.sku_bk` | товарная позиция партии |
| `purchase_line.sku_bk` | `catalog_service_db`: `sku.sku_bk` | закупаемая позиция |
| `movement.sku_bk` | `catalog_service_db`: `sku.sku_bk` | (вместе с batch_id — составной FK внутри сервиса) |
| `movement.src_bk / dst_bk` | `pos_service_db`: `pharmacy.pharmacy_bk` | если src_type/dst_type = 'pharmacy' (полиморфная ссылка) |
| `stock.location_bk` | `pos_service_db`: `pharmacy.pharmacy_bk` | если location_type = 'pharmacy' (полиморфная ссылка) |

## Защиты схемы (CHECK / EXCLUDE и триггеры)

CHECK/EXCLUDE: **26**, триггеров: **15** (полный список — в `migrations/wms/`).

- `batch.batch_batch_bk_check`: `CHECK ((batch_bk ~ '^BATCH-[0-9]{4}-[0-9]{5}$'::text))`
- `batch.batch_check`: `CHECK ((expiry_date > manufactured_date))`
- `batch.batch_not_expired_at_receipt`: `CHECK ((expiry_date >= ((received_at AT TIME ZONE 'UTC'::text))::date))`
- `batch.batch_sku_bk_check`: `CHECK ((sku_bk ~ '^SKU-[0-9]{6}$'::text))`
- `distribution_center.distribution_center_dc_bk_check`: `CHECK ((dc_bk ~ '^DC-[0-9]{2}$'::text))`
- `movement.movement_check`: `CHECK (((src_type IS NULL) = (src_bk IS NULL)))`
- `movement.movement_check1`: `CHECK (((dst_type IS NOT NULL) OR (dst_bk IS NULL)))`
- `movement.movement_check2`: `CHECK (((dst_type = ANY (ARRAY['dc'::text, 'pharmacy'::text])) = (dst_bk IS NOT NULL)))`
- `movement.movement_check3`: `CHECK (((dispatched_at IS NULL) OR (dispatched_at <= moved_at)))`
- `movement.movement_check4`: `CHECK (((src_type IS DISTINCT FROM dst_type) OR (src_bk IS DISTINCT FROM dst_bk)))`
- `movement.movement_dst_type_check`: `CHECK ((dst_type = ANY (ARRAY['dc'::text, 'pharmacy'::text, 'customer'::text])))`
- `movement.movement_movement_bk_check`: `CHECK ((movement_bk ~ '^MOV-[0-9]{9}$'::text))`
- `movement.movement_movement_type_check`: `CHECK ((movement_type = ANY (ARRAY['receipt'::text, 'transfer'::text, 'sale_writeoff'::text, 'return'::text, 'writeoff'::text])))`
- `movement.movement_qty_check`: `CHECK ((qty > (0)::numeric))`
- `movement.movement_src_type_check`: `CHECK ((src_type = ANY (ARRAY['dc'::text, 'pharmacy'::text, 'supplier'::text])))`
- `purchase_line.purchase_line_check`: `CHECK (((received_at IS NOT NULL) = (batch_id IS NOT NULL)))`
- `purchase_line.purchase_line_currency_check`: `CHECK (((currency)::text = upper((currency)::text)))`
- `purchase_line.purchase_line_line_no_check`: `CHECK ((line_no > 0))`
- `purchase_line.purchase_line_quantity_check`: `CHECK ((quantity > (0)::numeric))`
- `purchase_line.purchase_line_sku_bk_check`: `CHECK ((sku_bk ~ '^SKU-[0-9]{6}$'::text))`
- `purchase_line.purchase_line_unit_cost_check`: `CHECK ((unit_cost >= (0)::numeric))`
- `purchase_order.purchase_order_purchase_bk_check`: `CHECK ((purchase_bk ~ '^PO-[0-9]{4}-[0-9]{6}$'::text))`
- `stock.stock_location_type_check`: `CHECK ((location_type = ANY (ARRAY['dc'::text, 'pharmacy'::text])))`
- `stock.stock_not_negative`: `CHECK ((qty_on_hand >= (0)::numeric))`
- `supplier.supplier_supplier_bk_check`: `CHECK ((supplier_bk ~ '^SUP-[0-9]{4}$'::text))`
