-- ============================================================================
-- catalog_service_db — номенклатура, МНН, производители, цены с историей
-- ============================================================================
CREATE EXTENSION IF NOT EXISTS btree_gist;     -- нужен для EXCLUDE по (sku, scope) + интервал
CREATE SCHEMA catalog;

CREATE TABLE catalog.manufacturer (
    manufacturer_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    manufacturer_bk text NOT NULL UNIQUE CHECK (manufacturer_bk ~ '^MFR-[0-9]{4}$'),
    name            text NOT NULL,
    country         text NOT NULL,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'catalog_service'
);

CREATE TABLE catalog.drug_form (
    drug_form_id    bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    name            text NOT NULL UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- МНН (действующее вещество): один МНН -> много SKU (аналоги)
CREATE TABLE catalog.inn (
    inn_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    inn_bk          text NOT NULL UNIQUE CHECK (inn_bk ~ '^INN-[0-9]{4}$'),
    name            text NOT NULL UNIQUE,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE catalog.sku (
    sku_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku_bk          text NOT NULL UNIQUE CHECK (sku_bk ~ '^SKU-[0-9]{6}$'),     -- наружу отдаётся только BK
    sku_name        text NOT NULL,
    manufacturer_id bigint NOT NULL REFERENCES catalog.manufacturer,
    drug_form_id    bigint REFERENCES catalog.drug_form,
    category        text NOT NULL CHECK (category IN ('rx', 'otc', 'supplement', 'device', 'cosmetics')),
    is_rx           boolean NOT NULL,
    is_vital        boolean NOT NULL DEFAULT false,                             -- ЖНВЛП
    pack_qty        numeric(10, 2) NOT NULL CHECK (pack_qty > 0),
    is_active       boolean NOT NULL DEFAULT true,
    deleted_at      timestamptz,                                                -- soft delete
    attrs_valid_from timestamptz NOT NULL DEFAULT now(),                        -- с какого момента действует текущая версия названия/категории
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'catalog_service',
    CHECK (is_rx = (category = 'rx')),                                          -- признак рецептурности согласован с категорией
    CHECK (deleted_at IS NULL OR NOT is_active)
);

-- многие-ко-многим: комбинированные препараты содержат несколько МНН
CREATE TABLE catalog.sku_inn (
    sku_id          bigint NOT NULL REFERENCES catalog.sku,
    inn_id          bigint NOT NULL REFERENCES catalog.inn,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (sku_id, inn_id)
);
CREATE INDEX ON catalog.sku_inn (inn_id);

-- история атрибутов SKU, которые меняются (название, категория, рецептурность, ЖНВЛП)
CREATE TABLE catalog.sku_history (
    sku_id          bigint NOT NULL REFERENCES catalog.sku,
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz NOT NULL,
    sku_name        text NOT NULL,
    category        text NOT NULL,
    is_rx           boolean NOT NULL,
    is_vital        boolean NOT NULL,
    PRIMARY KEY (sku_id, valid_from),
    CHECK (valid_to > valid_from)
);

CREATE FUNCTION catalog.fn_sku_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.sku_name, OLD.category, OLD.is_rx, OLD.is_vital) IS DISTINCT FROM
       (NEW.sku_name, NEW.category, NEW.is_rx, NEW.is_vital) THEN
        INSERT INTO catalog.sku_history
            (sku_id, valid_from, valid_to, sku_name, category, is_rx, is_vital)
        VALUES (OLD.sku_id, OLD.attrs_valid_from, clock_timestamp(),
                OLD.sku_name, OLD.category, OLD.is_rx, OLD.is_vital);
        NEW.attrs_valid_from := clock_timestamp();
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_sku_history BEFORE UPDATE ON catalog.sku
    FOR EACH ROW EXECUTE FUNCTION catalog.fn_sku_history();

-- ---------------------------------------------------------------------------
-- Цены: SCD2 — одна строка = одна версия цены на интервале [valid_from, valid_to).
-- Область действия: chain ('ALL') / region (код региона) / pharmacy (BK аптеки).
-- ---------------------------------------------------------------------------
CREATE TABLE catalog.price (
    price_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    sku_id          bigint NOT NULL REFERENCES catalog.sku,
    price_scope     text NOT NULL CHECK (price_scope IN ('chain', 'region', 'pharmacy')),
    scope_bk        text NOT NULL,
    price           numeric(12, 2) NOT NULL CHECK (price > 0),
    currency        char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = upper(currency)),
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz,
    is_current      boolean NOT NULL,       -- хранимый флаг: детерминирован, не зависит от now()
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (is_current OR valid_to IS NOT NULL),             -- открытая версия обязана быть текущей
    CHECK ((price_scope = 'chain') = (scope_bk = 'ALL')),
    -- I5: интервалы одной (sku, область) не пересекаются
    CONSTRAINT price_no_overlap EXCLUDE USING gist (
        sku_id WITH =, price_scope WITH =, scope_bk WITH =,
        tstzrange(valid_from, valid_to, '[)') WITH &&)
);
-- I6: у области ровно одна текущая версия
CREATE UNIQUE INDEX price_one_current ON catalog.price (sku_id, price_scope, scope_bk)
    WHERE is_current;

-- Новая открытая версия цены закрывает предыдущую открытую версию той же области.
CREATE FUNCTION catalog.fn_price_close_previous() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.valid_to IS NULL THEN
        UPDATE catalog.price
           SET valid_to = NEW.valid_from, is_current = false
         WHERE sku_id = NEW.sku_id AND price_scope = NEW.price_scope
           AND scope_bk = NEW.scope_bk AND valid_to IS NULL
           AND valid_from < NEW.valid_from;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_price_close_previous BEFORE INSERT ON catalog.price
    FOR EACH ROW EXECUTE FUNCTION catalog.fn_price_close_previous();

-- ---------------------------------------------------------------------------
-- Стандартные гарантии: updated_at, неизменяемый BK, запрет физического DELETE
-- ---------------------------------------------------------------------------
SELECT util.add_touch(t::regclass) FROM unnest(ARRAY[
    'catalog.manufacturer', 'catalog.drug_form', 'catalog.inn', 'catalog.sku',
    'catalog.sku_inn', 'catalog.price']) t;
SELECT util.add_bk_guard('catalog.sku', 'sku_bk');
SELECT util.add_bk_guard('catalog.manufacturer', 'manufacturer_bk');
SELECT util.add_bk_guard('catalog.inn', 'inn_bk');
SELECT util.add_delete_guard(t::regclass) FROM unnest(ARRAY[
    'catalog.manufacturer', 'catalog.drug_form', 'catalog.inn', 'catalog.sku', 'catalog.price']) t;
