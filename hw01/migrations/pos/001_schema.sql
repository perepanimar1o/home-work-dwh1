-- ============================================================================
-- pos_service_db — аптеки, кассы, смены, кассовые документы, оплаты
-- ============================================================================
CREATE SCHEMA pos;

CREATE TABLE pos.pharmacy (
    pharmacy_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    pharmacy_bk     text NOT NULL UNIQUE CHECK (pharmacy_bk ~ '^PH-[0-9]{4}$'),
    pharmacy_name   text NOT NULL,
    region          text NOT NULL,
    city            text NOT NULL,
    address         text NOT NULL,
    opened_at       date NOT NULL,
    closed_at       date,
    is_active       boolean NOT NULL DEFAULT true,
    deleted_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'pos_service',
    CHECK (closed_at IS NULL OR closed_at >= opened_at),
    CHECK (is_active = (closed_at IS NULL))
);

-- адрес аптеки меняется (переезд): история
CREATE TABLE pos.pharmacy_history (
    pharmacy_id     bigint NOT NULL REFERENCES pos.pharmacy,
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz NOT NULL,
    city            text NOT NULL,
    address         text NOT NULL,
    PRIMARY KEY (pharmacy_id, valid_from),
    CHECK (valid_to > valid_from)
);
CREATE FUNCTION pos.fn_pharmacy_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.city, OLD.address) IS DISTINCT FROM (NEW.city, NEW.address) THEN
        INSERT INTO pos.pharmacy_history (pharmacy_id, valid_from, valid_to, city, address)
        VALUES (OLD.pharmacy_id, GREATEST(OLD.updated_at, OLD.created_at), clock_timestamp(),
                OLD.city, OLD.address)
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_pharmacy_history BEFORE UPDATE ON pos.pharmacy
    FOR EACH ROW EXECUTE FUNCTION pos.fn_pharmacy_history();

CREATE TABLE pos.register (
    register_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    register_bk     text NOT NULL UNIQUE CHECK (register_bk ~ '^REG-[0-9]{4}-[0-9]{2}$'),
    pharmacy_id     bigint NOT NULL REFERENCES pos.pharmacy,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON pos.register (pharmacy_id);

CREATE TABLE pos.cashier (
    cashier_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    cashier_bk      text NOT NULL UNIQUE CHECK (cashier_bk ~ '^CSH-[0-9]{6}$'),
    full_name       text NOT NULL,
    pharmacy_id     bigint NOT NULL REFERENCES pos.pharmacy,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE pos.shift (
    shift_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    shift_bk        text NOT NULL UNIQUE CHECK (shift_bk ~ '^SHF-[0-9]{8}-[0-9]{4}-[0-9]{2}$'),
    register_id     bigint NOT NULL REFERENCES pos.register,
    cashier_id      bigint NOT NULL REFERENCES pos.cashier,
    opened_at       timestamptz NOT NULL,
    closed_at       timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (closed_at IS NULL OR closed_at > opened_at)
);
CREATE INDEX ON pos.shift (register_id, opened_at);

CREATE TABLE pos.prescription (
    prescription_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    prescription_bk text NOT NULL UNIQUE CHECK (prescription_bk ~ '^RX-[0-9]{4}-[0-9]{7}$'),
    customer_bk     text,                   -- логическая ссылка в CRM (по BK, без FK)
    issued_at       timestamptz NOT NULL,
    doctor_name     text,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------
-- Локальные копии справочников других сервисов (кросс-сервисных FK нет!).
-- Нужны, чтобы инварианты I8/I10 проверялись СХЕМОЙ, а не приложением. См. ADR-0004.
-- Наполняются приложением/генератором из событий каталога и склада.
-- ---------------------------------------------------------------------------
CREATE TABLE pos.sku_ref (
    sku_bk          text PRIMARY KEY CHECK (sku_bk ~ '^SKU-[0-9]{6}$'),
    sku_name        text NOT NULL,
    is_rx           boolean NOT NULL,                 -- текущий признак в каталоге
    is_rx_since     timestamptz,                      -- с какого момента SKU рецептурный (NULL, если сейчас нет)
    synced_at       timestamptz NOT NULL DEFAULT now(),
    CHECK (NOT is_rx OR is_rx_since IS NOT NULL)
);
CREATE TABLE pos.batch_ref (
    batch_bk        text PRIMARY KEY CHECK (batch_bk ~ '^BATCH-[0-9]{4}-[0-9]{5}$'),
    sku_bk          text NOT NULL,
    expiry_date     date NOT NULL,
    synced_at       timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON pos.batch_ref (sku_bk);

-- ---------------------------------------------------------------------------
-- Кассовый документ
-- ---------------------------------------------------------------------------
CREATE TABLE pos.receipt (
    receipt_id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    receipt_bk          text NOT NULL UNIQUE CHECK (receipt_bk ~ '^RCPT-[0-9]{8}-[0-9]{6}$'),
    pharmacy_id         bigint NOT NULL REFERENCES pos.pharmacy,
    shift_id            bigint NOT NULL REFERENCES pos.shift,
    customer_bk         text,                               -- NULL: покупка без карты; ссылка в CRM по BK
    receipt_dt          timestamptz NOT NULL,
    doc_type            text NOT NULL CHECK (doc_type IN ('sale', 'refund')),
    parent_receipt_id   bigint REFERENCES pos.receipt,
    total_amount        numeric(14, 2) NOT NULL CHECK (total_amount >= 0),
    discount_amount     numeric(14, 2) NOT NULL DEFAULT 0 CHECK (discount_amount >= 0),
    currency            char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = upper(currency)),
    prescription_id     bigint REFERENCES pos.prescription,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    source_system       text NOT NULL DEFAULT 'pos_service',
    CHECK ((doc_type = 'refund') = (parent_receipt_id IS NOT NULL))
);
CREATE INDEX ON pos.receipt (receipt_dt);
CREATE INDEX ON pos.receipt (pharmacy_id, receipt_dt);
CREATE INDEX ON pos.receipt (parent_receipt_id) WHERE parent_receipt_id IS NOT NULL;
CREATE INDEX ON pos.receipt (updated_at);

CREATE TABLE pos.receipt_line (
    receipt_id          bigint NOT NULL REFERENCES pos.receipt,
    line_no             integer NOT NULL CHECK (line_no > 0),
    sku_bk              text NOT NULL,                      -- логическая ссылка в каталог
    batch_bk            text NOT NULL,                      -- логическая ссылка на склад: продаём из конкретной серии
    quantity            numeric(12, 3) NOT NULL CHECK (quantity > 0),
    unit_price          numeric(12, 2) NOT NULL CHECK (unit_price >= 0),     -- snapshot цены на момент продажи
    line_discount       numeric(12, 2) NOT NULL DEFAULT 0 CHECK (line_discount >= 0),
    is_rx_snapshot      boolean NOT NULL,                   -- snapshot: рецептурность на момент продажи
    sku_name_snapshot   text NOT NULL,                      -- snapshot: название на момент продажи
    -- I4: арифметика позиции гарантируется самой схемой (вычисляемая колонка)
    line_amount         numeric(14, 2) GENERATED ALWAYS AS
                            (round(quantity * unit_price - line_discount, 2)) STORED,
    created_at          timestamptz NOT NULL DEFAULT now(),
    updated_at          timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (receipt_id, line_no),
    CONSTRAINT line_discount_not_above_gross CHECK (line_discount <= quantity * unit_price)
);
CREATE INDEX ON pos.receipt_line (sku_bk);
CREATE INDEX ON pos.receipt_line (batch_bk);

CREATE TABLE pos.payment (
    receipt_id      bigint NOT NULL REFERENCES pos.receipt,
    payment_no      integer NOT NULL CHECK (payment_no > 0),
    payment_method  text NOT NULL CHECK (payment_method IN ('cash', 'card', 'bonus', 'certificate')),
    amount          numeric(14, 2) NOT NULL CHECK (amount > 0),
    currency        char(3) NOT NULL DEFAULT 'RUB' CHECK (currency = upper(currency)),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (receipt_id, payment_no)
);

CREATE TABLE pos.seed_log (
    seed_log_id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,   -- PK нужен для REPLICA IDENTITY (CDC)
    seed        integer NOT NULL,
    kind        text NOT NULL,
    receipts    integer NOT NULL,
    ran_at      timestamptz NOT NULL DEFAULT now()
);

-- ===========================================================================
-- Триггеры инвариантов
-- ===========================================================================

-- Документ оформляется на открытой смене; возврат ссылается на существующую продажу.
CREATE FUNCTION pos.fn_receipt_before() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    s record;
    p record;
BEGIN
    SELECT opened_at, closed_at INTO s FROM pos.shift WHERE shift_id = NEW.shift_id;
    IF NEW.receipt_dt < s.opened_at OR (s.closed_at IS NOT NULL AND NEW.receipt_dt > s.closed_at) THEN
        RAISE EXCEPTION 'документ % оформлен вне открытой смены', NEW.receipt_bk USING ERRCODE = 'P0001';
    END IF;
    IF NEW.doc_type = 'refund' THEN
        SELECT doc_type, receipt_dt INTO p FROM pos.receipt WHERE receipt_id = NEW.parent_receipt_id;
        IF p.doc_type IS DISTINCT FROM 'sale' THEN
            RAISE EXCEPTION 'возврат % должен ссылаться на документ продажи', NEW.receipt_bk USING ERRCODE = 'P0001';
        END IF;
        IF NEW.receipt_dt < p.receipt_dt THEN
            RAISE EXCEPTION 'возврат % раньше продажи', NEW.receipt_bk USING ERRCODE = 'P0001';
        END IF;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_receipt_before BEFORE INSERT ON pos.receipt
    FOR EACH ROW EXECUTE FUNCTION pos.fn_receipt_before();

-- I8 (не продаём просрочку), X09 (SKU позиции = SKU партии), snapshot рецептурности.
CREATE FUNCTION pos.fn_line_before() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    r record;
    b record;
    k record;
BEGIN
    SELECT receipt_dt, doc_type, receipt_bk INTO r FROM pos.receipt WHERE receipt_id = NEW.receipt_id;
    SELECT sku_bk, expiry_date INTO b FROM pos.batch_ref WHERE batch_bk = NEW.batch_bk;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'неизвестная партия % (позиция % документа %)', NEW.batch_bk, NEW.line_no, r.receipt_bk
            USING ERRCODE = 'P0001';
    END IF;
    IF b.sku_bk <> NEW.sku_bk THEN
        RAISE EXCEPTION 'SKU позиции (%) не совпадает с SKU партии % (%)', NEW.sku_bk, NEW.batch_bk, b.sku_bk
            USING ERRCODE = 'P0001';
    END IF;
    IF r.doc_type = 'sale' AND (r.receipt_dt AT TIME ZONE 'UTC')::date > b.expiry_date THEN
        RAISE EXCEPTION 'нельзя продать партию % с истёкшим сроком годности (до %)', NEW.batch_bk, b.expiry_date
            USING ERRCODE = 'P0001';
    END IF;
    SELECT is_rx, is_rx_since INTO k FROM pos.sku_ref WHERE sku_bk = NEW.sku_bk;
    IF NOT FOUND THEN
        RAISE EXCEPTION 'неизвестный SKU %', NEW.sku_bk USING ERRCODE = 'P0001';
    END IF;
    -- рецептурную позицию нельзя пометить как безрецептурную (после даты перевода в rx)
    IF k.is_rx AND NOT NEW.is_rx_snapshot AND r.receipt_dt >= k.is_rx_since THEN
        RAISE EXCEPTION 'SKU % рецептурный: is_rx_snapshot обязан быть true', NEW.sku_bk USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_line_before BEFORE INSERT ON pos.receipt_line
    FOR EACH ROW EXECUTE FUNCTION pos.fn_line_before();

-- I11: возврат не превышает проданное (с учётом ранее оформленных возвратов).
CREATE FUNCTION pos.fn_refund_limit() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    rc record;
    sold numeric;
    returned numeric;
BEGIN
    SELECT doc_type, parent_receipt_id, receipt_bk INTO rc FROM pos.receipt WHERE receipt_id = NEW.receipt_id;
    IF rc.doc_type <> 'refund' THEN
        RETURN NULL;
    END IF;
    PERFORM pg_advisory_xact_lock(hashtextextended('refund:' || rc.parent_receipt_id, 0));
    SELECT sum(quantity) INTO sold FROM pos.receipt_line
     WHERE receipt_id = rc.parent_receipt_id AND sku_bk = NEW.sku_bk;
    SELECT sum(l.quantity) INTO returned
      FROM pos.receipt_line l JOIN pos.receipt r ON r.receipt_id = l.receipt_id
     WHERE r.doc_type = 'refund' AND r.parent_receipt_id = rc.parent_receipt_id AND l.sku_bk = NEW.sku_bk;
    IF sold IS NULL OR returned > sold THEN
        RAISE EXCEPTION 'возврат % по SKU %: вернуть % при проданных %', rc.receipt_bk, NEW.sku_bk,
            returned, coalesce(sold, 0) USING ERRCODE = 'P0001';
    END IF;
    RETURN NULL;
END $$;
CREATE TRIGGER trg_refund_limit AFTER INSERT OR UPDATE OF quantity, sku_bk ON pos.receipt_line
    FOR EACH ROW EXECUTE FUNCTION pos.fn_refund_limit();

-- I3 + I12 + I10: согласованность документа проверяется в КОНЦЕ транзакции
-- (при вставке документа позиций и оплат ещё нет — обычный AFTER-триггер здесь не годится).
CREATE FUNCTION pos.fn_check_receipt() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    rid       bigint;
    r         record;
    lcnt      integer;
    lsum      numeric;
    dsum      numeric;
    psum      numeric;
    has_rx    boolean;
BEGIN
    IF TG_OP = 'DELETE' THEN rid := OLD.receipt_id; ELSE rid := NEW.receipt_id; END IF;
    SELECT * INTO r FROM pos.receipt WHERE receipt_id = rid;
    IF NOT FOUND THEN
        RETURN NULL;
    END IF;
    SELECT count(*), coalesce(sum(line_amount), 0), coalesce(sum(line_discount), 0),
           coalesce(bool_or(is_rx_snapshot), false)
      INTO lcnt, lsum, dsum, has_rx
      FROM pos.receipt_line WHERE receipt_id = rid;
    IF lcnt = 0 THEN
        RAISE EXCEPTION 'документ % без позиций', r.receipt_bk USING ERRCODE = 'P0001';
    END IF;
    IF lsum <> r.total_amount THEN
        RAISE EXCEPTION 'документ %: total_amount % не равен сумме позиций %', r.receipt_bk,
            r.total_amount, lsum USING ERRCODE = 'P0001';
    END IF;
    IF dsum <> r.discount_amount THEN
        RAISE EXCEPTION 'документ %: discount_amount % не равен сумме скидок позиций %', r.receipt_bk,
            r.discount_amount, dsum USING ERRCODE = 'P0001';
    END IF;
    SELECT coalesce(sum(amount), 0) INTO psum FROM pos.payment WHERE receipt_id = rid;
    IF psum <> r.total_amount THEN
        RAISE EXCEPTION 'документ %: сумма оплат % не равна сумме документа %', r.receipt_bk,
            psum, r.total_amount USING ERRCODE = 'P0001';
    END IF;
    IF has_rx AND r.prescription_id IS NULL THEN
        RAISE EXCEPTION 'документ %: рецептурный препарат без рецепта', r.receipt_bk USING ERRCODE = 'P0001';
    END IF;
    RETURN NULL;
END $$;

CREATE CONSTRAINT TRIGGER ctrg_receipt_consistency AFTER INSERT OR UPDATE ON pos.receipt
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pos.fn_check_receipt();
CREATE CONSTRAINT TRIGGER ctrg_line_consistency AFTER INSERT OR UPDATE OR DELETE ON pos.receipt_line
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pos.fn_check_receipt();
CREATE CONSTRAINT TRIGGER ctrg_payment_consistency AFTER INSERT OR UPDATE OR DELETE ON pos.payment
    DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION pos.fn_check_receipt();

-- ---------------------------------------------------------------------------
-- Стандартные гарантии
-- ---------------------------------------------------------------------------
SELECT util.add_touch(t::regclass) FROM unnest(ARRAY[
    'pos.pharmacy', 'pos.register', 'pos.cashier', 'pos.shift', 'pos.prescription',
    'pos.receipt', 'pos.receipt_line', 'pos.payment']) t;
SELECT util.add_bk_guard('pos.pharmacy', 'pharmacy_bk');
SELECT util.add_bk_guard('pos.receipt', 'receipt_bk');
SELECT util.add_bk_guard('pos.shift', 'shift_bk');
SELECT util.add_bk_guard('pos.cashier', 'cashier_bk');
SELECT util.add_bk_guard('pos.register', 'register_bk');
SELECT util.add_bk_guard('pos.prescription', 'prescription_bk');
SELECT util.add_delete_guard(t::regclass) FROM unnest(ARRAY[
    'pos.pharmacy', 'pos.register', 'pos.cashier']) t;
