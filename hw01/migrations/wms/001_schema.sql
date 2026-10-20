-- ============================================================================
-- wms_service_db — поставщики, распредцентры, закупки, партии, движения, остатки
-- ============================================================================
CREATE SCHEMA wms;

CREATE TABLE wms.supplier (
    supplier_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    supplier_bk     text NOT NULL UNIQUE CHECK (supplier_bk ~ '^SUP-[0-9]{4}$'),
    supplier_name   text NOT NULL,
    country         text NOT NULL,
    is_active       boolean NOT NULL DEFAULT true,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'wms_service'
);

CREATE TABLE wms.distribution_center (
    dc_id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    dc_bk           text NOT NULL UNIQUE CHECK (dc_bk ~ '^DC-[0-9]{2}$'),
    dc_name         text NOT NULL,
    region          text NOT NULL,
    city            text NOT NULL,
    is_active       boolean NOT NULL DEFAULT true,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'wms_service'
);

-- Партия = (SKU, серия производителя, даты производства и годности)
CREATE TABLE wms.batch (
    batch_id            bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    batch_bk            text NOT NULL UNIQUE CHECK (batch_bk ~ '^BATCH-[0-9]{4}-[0-9]{5}$'),
    sku_bk              text NOT NULL CHECK (sku_bk ~ '^SKU-[0-9]{6}$'),    -- логическая ссылка в каталог
    series_no           text NOT NULL,
    manufactured_date   date NOT NULL,
    expiry_date         date NOT NULL,
    received_at         timestamptz NOT NULL,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    source_system       text NOT NULL DEFAULT 'wms_service',
    CONSTRAINT batch_sku_series_unique UNIQUE (sku_bk, series_no),
    CONSTRAINT batch_id_sku_unique UNIQUE (batch_id, sku_bk),
    CHECK (expiry_date > manufactured_date),
    -- I9: партия с истёкшим на момент приёмки сроком не принимается
    CONSTRAINT batch_not_expired_at_receipt CHECK (expiry_date >= (received_at AT TIME ZONE 'UTC')::date)
);
CREATE INDEX ON wms.batch (sku_bk);

CREATE TABLE wms.purchase_order (
    purchase_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    purchase_bk     text NOT NULL UNIQUE CHECK (purchase_bk ~ '^PO-[0-9]{4}-[0-9]{6}$'),
    supplier_id     bigint NOT NULL REFERENCES wms.supplier,
    dc_id           bigint NOT NULL REFERENCES wms.distribution_center,
    ordered_at      timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE wms.purchase_line (
    purchase_id     bigint NOT NULL REFERENCES wms.purchase_order,
    line_no         integer NOT NULL CHECK (line_no > 0),
    sku_bk          text NOT NULL CHECK (sku_bk ~ '^SKU-[0-9]{6}$'),
    batch_id        bigint REFERENCES wms.batch,            -- NULL, пока заказ не принят
    quantity        numeric(12, 3) NOT NULL CHECK (quantity > 0),
    unit_cost       numeric(12, 2) NOT NULL CHECK (unit_cost >= 0),
    line_cost       numeric(14, 2) GENERATED ALWAYS AS (round(quantity * unit_cost, 2)) STORED,
    currency        char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = upper(currency)),
    received_at     timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (purchase_id, line_no),
    CHECK ((received_at IS NOT NULL) = (batch_id IS NOT NULL))     -- принятая строка имеет партию и дату
);
CREATE INDEX ON wms.purchase_line (sku_bk);
CREATE INDEX ON wms.purchase_line (updated_at);

-- Движение партии. Остаток материализуется триггером (ADR-0003).
CREATE TABLE wms.movement (
    movement_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    movement_bk     text NOT NULL UNIQUE CHECK (movement_bk ~ '^MOV-[0-9]{9}$'),
    batch_id        bigint NOT NULL,
    sku_bk          text NOT NULL,
    movement_type   text NOT NULL CHECK (movement_type IN ('receipt', 'transfer', 'sale_writeoff', 'return', 'writeoff')),
    src_type        text CHECK (src_type IN ('dc', 'pharmacy', 'supplier')),
    src_bk          text,
    dst_type        text CHECK (dst_type IN ('dc', 'pharmacy', 'customer')),
    dst_bk          text,
    counterparty_bk text,                                   -- поставщик для 'return' (возврат поставщику)
    qty             numeric(12, 3) NOT NULL CHECK (qty > 0),
    dispatched_at   timestamptz,
    moved_at        timestamptz NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'wms_service',
    FOREIGN KEY (batch_id, sku_bk) REFERENCES wms.batch (batch_id, sku_bk),
    CHECK ((src_type IS NULL) = (src_bk IS NULL)),
    CHECK (dst_type IS NOT NULL OR dst_bk IS NULL),
    CHECK ((dst_type IN ('dc', 'pharmacy')) = (dst_bk IS NOT NULL)),
    CHECK (dispatched_at IS NULL OR dispatched_at <= moved_at),
    CHECK (src_type IS DISTINCT FROM dst_type OR src_bk IS DISTINCT FROM dst_bk),
    CHECK (
           (movement_type = 'receipt'       AND src_type = 'supplier' AND dst_type = 'dc')
        OR (movement_type = 'transfer'      AND src_type IN ('dc', 'pharmacy') AND dst_type IN ('dc', 'pharmacy'))
        OR (movement_type = 'sale_writeoff' AND src_type = 'pharmacy' AND dst_type = 'customer')
        OR (movement_type IN ('return', 'writeoff') AND src_type IN ('dc', 'pharmacy') AND dst_type IS NULL)
    )
);
CREATE INDEX ON wms.movement (batch_id);
CREATE INDEX ON wms.movement (moved_at);
CREATE INDEX ON wms.movement (updated_at);

-- Остаток партии в локации. Локация полиморфна (dc | pharmacy) — FK невозможен (см. dwh_mapping §7).
CREATE TABLE wms.stock (
    location_type   text NOT NULL CHECK (location_type IN ('dc', 'pharmacy')),
    location_bk     text NOT NULL,
    batch_id        bigint NOT NULL REFERENCES wms.batch,
    qty_on_hand     numeric(12, 3) NOT NULL,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (location_type, location_bk, batch_id),
    -- I7: остаток не может стать отрицательным
    CONSTRAINT stock_not_negative CHECK (qty_on_hand >= 0)
);
CREATE INDEX ON wms.stock (batch_id);

CREATE FUNCTION wms.fn_apply_movement() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.src_type IN ('dc', 'pharmacy') THEN
        -- UPDATE, а не INSERT .. ON CONFLICT: CHECK проверяется для предлагаемой строки
        -- ещё до разрешения конфликта и ломал бы законное списание
        UPDATE wms.stock SET qty_on_hand = qty_on_hand - NEW.qty
         WHERE location_type = NEW.src_type AND location_bk = NEW.src_bk AND batch_id = NEW.batch_id;
        IF NOT FOUND THEN
            RAISE EXCEPTION 'на % % нет остатка партии % для движения %', NEW.src_type, NEW.src_bk,
                NEW.batch_id, NEW.movement_bk USING ERRCODE = '23514';
        END IF;
    END IF;
    IF NEW.dst_type IN ('dc', 'pharmacy') THEN
        INSERT INTO wms.stock (location_type, location_bk, batch_id, qty_on_hand)
        VALUES (NEW.dst_type, NEW.dst_bk, NEW.batch_id, NEW.qty)
        ON CONFLICT (location_type, location_bk, batch_id)
        DO UPDATE SET qty_on_hand = wms.stock.qty_on_hand + EXCLUDED.qty_on_hand;
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER trg_apply_movement AFTER INSERT ON wms.movement
    FOR EACH ROW EXECUTE FUNCTION wms.fn_apply_movement();

-- Журнал движений неизменяем: исправление = встречное движение, а не UPDATE/DELETE
CREATE FUNCTION wms.fn_movement_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'журнал движений неизменяем: используйте встречное движение' USING ERRCODE = 'P0001';
END $$;
CREATE TRIGGER trg_movement_immutable BEFORE UPDATE OR DELETE ON wms.movement
    FOR EACH ROW EXECUTE FUNCTION wms.fn_movement_immutable();

-- ---------------------------------------------------------------------------
-- Стандартные гарантии
-- ---------------------------------------------------------------------------
SELECT util.add_touch(t::regclass) FROM unnest(ARRAY[
    'wms.supplier', 'wms.distribution_center', 'wms.batch', 'wms.purchase_order',
    'wms.purchase_line', 'wms.stock']) t;
SELECT util.add_bk_guard('wms.supplier', 'supplier_bk');
SELECT util.add_bk_guard('wms.distribution_center', 'dc_bk');
SELECT util.add_bk_guard('wms.batch', 'batch_bk');
SELECT util.add_bk_guard('wms.purchase_order', 'purchase_bk');
SELECT util.add_delete_guard(t::regclass) FROM unnest(ARRAY[
    'wms.supplier', 'wms.distribution_center', 'wms.batch']) t;
