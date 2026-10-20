# ER-диаграмма: pos_service_db

Аптеки, кассы, смены, кассовые документы, оплаты. Схема `pos` (базовые таблицы) и интерфейс `contract` (представления для DWH).

Диаграмма **сгенерирована** из системных каталогов работающей базы (`python scripts/gen_erd.py`), поэтому отражает фактическую схему.
Обозначения: `PK` — первичный ключ, `FK` — внешний ключ (только внутри сервиса), `UK` — уникальный ключ (в т.ч. business key). В комментарии: `NOT NULL` / `NULL` — обязательность, `GENERATED` — вычисляемая колонка, `IDENTITY` — суррогатный ключ.

```mermaid
erDiagram
    batch_ref {
        text batch_bk PK "NOT NULL"
        text sku_bk "NOT NULL"
        date expiry_date "NOT NULL"
        timestamptz synced_at "NOT NULL; DEFAULT now"
    }
    cashier {
        bigint cashier_id PK "NOT NULL; IDENTITY"
        text cashier_bk UK "NOT NULL"
        text full_name "NOT NULL"
        bigint pharmacy_id FK "NOT NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    payment {
        bigint receipt_id PK, FK "NOT NULL"
        integer payment_no PK "NOT NULL"
        text payment_method "NOT NULL"
        numeric_14_2 amount "NOT NULL"
        char currency "NOT NULL; DEFAULT RUB"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    pharmacy {
        bigint pharmacy_id PK "NOT NULL; IDENTITY"
        text pharmacy_bk UK "NOT NULL"
        text pharmacy_name "NOT NULL"
        text region "NOT NULL"
        text city "NOT NULL"
        text address "NOT NULL"
        date opened_at "NOT NULL"
        date closed_at "NULL"
        boolean is_active "NOT NULL; DEFAULT true"
        timestamptz deleted_at "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
        text source_system "NOT NULL; DEFAULT pos_service"
    }
    pharmacy_history {
        bigint pharmacy_id PK, FK "NOT NULL"
        timestamptz valid_from PK "NOT NULL"
        timestamptz valid_to "NOT NULL"
        text city "NOT NULL"
        text address "NOT NULL"
    }
    prescription {
        bigint prescription_id PK "NOT NULL; IDENTITY"
        text prescription_bk UK "NOT NULL"
        text customer_bk "NULL"
        timestamptz issued_at "NOT NULL"
        text doctor_name "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    receipt {
        bigint receipt_id PK "NOT NULL; IDENTITY"
        text receipt_bk UK "NOT NULL"
        bigint pharmacy_id FK "NOT NULL"
        bigint shift_id FK "NOT NULL"
        text customer_bk "NULL"
        timestamptz receipt_dt "NOT NULL"
        text doc_type "NOT NULL"
        bigint parent_receipt_id FK "NULL"
        numeric_14_2 total_amount "NOT NULL"
        numeric_14_2 discount_amount "NOT NULL; DEFAULT 0"
        char currency "NOT NULL; DEFAULT RUB"
        bigint prescription_id FK "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
        text source_system "NOT NULL; DEFAULT pos_service"
    }
    receipt_line {
        bigint receipt_id PK, FK "NOT NULL"
        integer line_no PK "NOT NULL"
        text sku_bk "NOT NULL"
        text batch_bk "NOT NULL"
        numeric_12_3 quantity "NOT NULL"
        numeric_12_2 unit_price "NOT NULL"
        numeric_12_2 line_discount "NOT NULL; DEFAULT 0"
        boolean is_rx_snapshot "NOT NULL"
        text sku_name_snapshot "NOT NULL"
        numeric_14_2 line_amount "NULL; GENERATED"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    register {
        bigint register_id PK "NOT NULL; IDENTITY"
        text register_bk UK "NOT NULL"
        bigint pharmacy_id FK "NOT NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    seed_log {
        bigint seed_log_id PK "NOT NULL; IDENTITY"
        integer seed "NOT NULL"
        text kind "NOT NULL"
        integer receipts "NOT NULL"
        timestamptz ran_at "NOT NULL; DEFAULT now"
    }
    shift {
        bigint shift_id PK "NOT NULL; IDENTITY"
        text shift_bk UK "NOT NULL"
        bigint register_id FK "NOT NULL"
        bigint cashier_id FK "NOT NULL"
        timestamptz opened_at "NOT NULL"
        timestamptz closed_at "NULL"
        timestamptz created_at "NOT NULL; DEFAULT now"
        timestamptz updated_at "NOT NULL; DEFAULT now"
    }
    sku_ref {
        text sku_bk PK "NOT NULL"
        text sku_name "NOT NULL"
        boolean is_rx "NOT NULL"
        timestamptz is_rx_since "NULL"
        timestamptz synced_at "NOT NULL; DEFAULT now"
    }
    pharmacy ||--o{ cashier : "pharmacy_id"
    receipt ||--o{ payment : "receipt_id"
    pharmacy ||--o{ pharmacy_history : "pharmacy_id"
    receipt ||--o{ receipt : "parent_receipt_id"
    pharmacy ||--o{ receipt : "pharmacy_id"
    prescription ||--o{ receipt : "prescription_id"
    shift ||--o{ receipt : "shift_id"
    receipt ||--o{ receipt_line : "receipt_id"
    pharmacy ||--o{ register : "pharmacy_id"
    cashier ||--o{ shift : "cashier_id"
    register ||--o{ shift : "register_id"
```

## Интерфейс `contract` (представления)

| представление | колонки (имя тип) |
|---|---|
| `contract.v_payment` | receipt_bk text, payment_no integer, payment_method text, amount numeric(14,2), currency text, updated_at timestamptz |
| `contract.v_pharmacy` | pharmacy_bk text, pharmacy_name text, region text, city text, address text, opened_at date, closed_at date, is_active boolean, updated_at timestamptz |
| `contract.v_receipt` | receipt_bk text, pharmacy_bk text, customer_bk text, cashier_bk text, shift_bk text, receipt_dt timestamptz, doc_type text, parent_receipt_bk text, total_amount numeric(14,2), discount_amount numeric(14,2), currency text, prescription_bk text, updated_at timestamptz |
| `contract.v_receipt_line` | receipt_bk text, line_no integer, sku text, batch_bk text, quantity numeric(12,3), unit_price numeric(12,2), line_discount numeric(12,2), line_amount numeric(14,2), is_rx_snapshot boolean, sku_name_snapshot text, updated_at timestamptz |

## Логические ссылки на другие сервисы (по business key, **без FOREIGN KEY**)

| колонка этого сервиса | ссылается на | комментарий |
|---|---|---|
| `receipt.customer_bk` | `crm_service_db`: `customer.customer_bk` | покупатель; NULL = покупка без карты |
| `prescription.customer_bk` | `crm_service_db`: `customer.customer_bk` | пациент по рецепту |
| `receipt_line.sku_bk` | `catalog_service_db`: `sku.sku_bk` | позиция каталога (+ локальная копия sku_ref) |
| `receipt_line.batch_bk` | `wms_service_db`: `batch.batch_bk` | серия, из которой продано (+ локальная копия batch_ref) |
| `sku_ref.sku_bk` | `catalog_service_db`: `sku.sku_bk` | копия справочника для проверки I8/I10 |
| `batch_ref.batch_bk` | `wms_service_db`: `batch.batch_bk` | копия справочника для проверки I8 |
| `batch_ref.sku_bk` | `catalog_service_db`: `sku.sku_bk` |  |

## Защиты схемы (CHECK / EXCLUDE и триггеры)

CHECK/EXCLUDE: **27**, триггеров: **24** (полный список — в `migrations/pos/`).

- `batch_ref.batch_ref_batch_bk_check`: `CHECK ((batch_bk ~ '^BATCH-[0-9]{4}-[0-9]{5}$'::text))`
- `cashier.cashier_cashier_bk_check`: `CHECK ((cashier_bk ~ '^CSH-[0-9]{6}$'::text))`
- `payment.payment_amount_check`: `CHECK ((amount > (0)::numeric))`
- `payment.payment_currency_check`: `CHECK (((currency)::text = upper((currency)::text)))`
- `payment.payment_payment_method_check`: `CHECK ((payment_method = ANY (ARRAY['cash'::text, 'card'::text, 'bonus'::text, 'certificate'::text])))`
- `payment.payment_payment_no_check`: `CHECK ((payment_no > 0))`
- `pharmacy.pharmacy_check`: `CHECK (((closed_at IS NULL) OR (closed_at >= opened_at)))`
- `pharmacy.pharmacy_check1`: `CHECK ((is_active = (closed_at IS NULL)))`
- `pharmacy.pharmacy_pharmacy_bk_check`: `CHECK ((pharmacy_bk ~ '^PH-[0-9]{4}$'::text))`
- `pharmacy_history.pharmacy_history_check`: `CHECK ((valid_to > valid_from))`
- `prescription.prescription_prescription_bk_check`: `CHECK ((prescription_bk ~ '^RX-[0-9]{4}-[0-9]{7}$'::text))`
- `receipt.receipt_check`: `CHECK (((doc_type = 'refund'::text) = (parent_receipt_id IS NOT NULL)))`
- `receipt.receipt_currency_check`: `CHECK (((currency)::text = upper((currency)::text)))`
- `receipt.receipt_discount_amount_check`: `CHECK ((discount_amount >= (0)::numeric))`
- `receipt.receipt_doc_type_check`: `CHECK ((doc_type = ANY (ARRAY['sale'::text, 'refund'::text])))`
- `receipt.receipt_receipt_bk_check`: `CHECK ((receipt_bk ~ '^RCPT-[0-9]{8}-[0-9]{6}$'::text))`
- `receipt.receipt_total_amount_check`: `CHECK ((total_amount >= (0)::numeric))`
- `receipt_line.line_discount_not_above_gross`: `CHECK ((line_discount <= (quantity * unit_price)))`
- `receipt_line.receipt_line_line_discount_check`: `CHECK ((line_discount >= (0)::numeric))`
- `receipt_line.receipt_line_line_no_check`: `CHECK ((line_no > 0))`
- `receipt_line.receipt_line_quantity_check`: `CHECK ((quantity > (0)::numeric))`
- `receipt_line.receipt_line_unit_price_check`: `CHECK ((unit_price >= (0)::numeric))`
- `register.register_register_bk_check`: `CHECK ((register_bk ~ '^REG-[0-9]{4}-[0-9]{2}$'::text))`
- `shift.shift_check`: `CHECK (((closed_at IS NULL) OR (closed_at > opened_at)))`
- `shift.shift_shift_bk_check`: `CHECK ((shift_bk ~ '^SHF-[0-9]{8}-[0-9]{4}-[0-9]{2}$'::text))`
- `sku_ref.sku_ref_check`: `CHECK (((NOT is_rx) OR (is_rx_since IS NOT NULL)))`
- `sku_ref.sku_ref_sku_bk_check`: `CHECK ((sku_bk ~ '^SKU-[0-9]{6}$'::text))`
