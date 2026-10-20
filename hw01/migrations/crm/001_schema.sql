-- ============================================================================
-- crm_service_db — клиенты, контакты, согласия, карты лояльности
-- ============================================================================
CREATE SCHEMA crm;

CREATE TABLE crm.customer (
    customer_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_bk     text NOT NULL UNIQUE CHECK (customer_bk ~ '^CUST-[0-9]{6}$'),
    full_name       text,                                   -- ПДн: обнуляется при удалении клиента
    birth_year      integer CHECK (birth_year BETWEEN 1900 AND 2100),
    sex             text CHECK (sex IN ('M', 'F')),
    city            text,
    region          text,
    loyalty_level   text CHECK (loyalty_level IN ('basic', 'silver', 'gold', 'platinum')),
    registered_at   timestamptz NOT NULL,
    deleted_at      timestamptz,                            -- soft delete
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    source_system   text NOT NULL DEFAULT 'crm_service'
);

-- история атрибутов, которые меняются со временем (город, уровень лояльности)
CREATE TABLE crm.customer_history (
    customer_id     bigint NOT NULL REFERENCES crm.customer,
    valid_from      timestamptz NOT NULL,
    valid_to        timestamptz NOT NULL,
    city            text,
    region          text,
    loyalty_level   text,
    PRIMARY KEY (customer_id, valid_from),
    CHECK (valid_to > valid_from)
);

CREATE TABLE crm.customer_contact (
    contact_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     bigint NOT NULL REFERENCES crm.customer,
    contact_type    text NOT NULL CHECK (contact_type IN ('phone', 'email')),
    value           text,                                   -- NULL = обезличено
    is_primary      boolean NOT NULL DEFAULT false,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ON crm.customer_contact (customer_id);

-- Согласия версионируются; активное согласие данного типа — только одно.
CREATE TABLE crm.consent (
    consent_id      bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    customer_id     bigint NOT NULL REFERENCES crm.customer,
    consent_type    text NOT NULL CHECK (consent_type IN ('personal_data', 'marketing')),
    granted_at      timestamptz NOT NULL,
    revoked_at      timestamptz,
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK (revoked_at IS NULL OR revoked_at >= granted_at)
);
CREATE UNIQUE INDEX consent_one_active ON crm.consent (customer_id, consent_type)
    WHERE revoked_at IS NULL;

CREATE TABLE crm.loyalty_card (
    card_id         bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    card_bk         text NOT NULL UNIQUE CHECK (card_bk ~ '^CARD-[0-9]{8}$'),
    customer_id     bigint NOT NULL REFERENCES crm.customer,
    issued_at       timestamptz NOT NULL,
    closed_at       timestamptz,
    status          text NOT NULL CHECK (status IN ('active', 'blocked', 'closed')),
    bonus_balance   numeric(12, 2) NOT NULL DEFAULT 0 CHECK (bonus_balance >= 0),
    created_at      timestamptz NOT NULL DEFAULT now(),
    updated_at      timestamptz NOT NULL DEFAULT now(),
    CHECK ((status = 'closed') = (closed_at IS NOT NULL)),
    CHECK (closed_at IS NULL OR closed_at >= issued_at)
);
-- у клиента не больше одной активной карты
CREATE UNIQUE INDEX card_one_active ON crm.loyalty_card (customer_id) WHERE status = 'active';
CREATE INDEX ON crm.loyalty_card (customer_id);

-- ---------------------------------------------------------------------------
-- История: перед изменением города/региона/уровня сохраняем прежнюю версию
-- ---------------------------------------------------------------------------
CREATE FUNCTION crm.fn_customer_history() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.city, OLD.region, OLD.loyalty_level) IS DISTINCT FROM
       (NEW.city, NEW.region, NEW.loyalty_level) THEN
        INSERT INTO crm.customer_history
            (customer_id, valid_from, valid_to, city, region, loyalty_level)
        VALUES (OLD.customer_id, GREATEST(OLD.updated_at, OLD.registered_at),
                clock_timestamp(), OLD.city, OLD.region, OLD.loyalty_level)
        ON CONFLICT DO NOTHING;
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_customer_history BEFORE UPDATE ON crm.customer
    FOR EACH ROW EXECUTE FUNCTION crm.fn_customer_history();

-- ---------------------------------------------------------------------------
-- Soft delete клиента: физически строка остаётся (нужна история продаж),
-- но ПДн обнуляются, согласия отзываются, карты закрываются.
-- ---------------------------------------------------------------------------
CREATE FUNCTION crm.fn_customer_soft_delete() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.deleted_at IS NOT NULL AND NEW.deleted_at IS NULL THEN
        RAISE EXCEPTION 'клиент % удалён, восстановление запрещено', OLD.customer_bk
            USING ERRCODE = 'P0001';
    END IF;
    IF OLD.deleted_at IS NULL AND NEW.deleted_at IS NOT NULL THEN
        NEW.full_name := NULL;
        UPDATE crm.customer_contact SET value = NULL, is_primary = false
         WHERE customer_id = NEW.customer_id;
        UPDATE crm.consent SET revoked_at = GREATEST(granted_at, NEW.deleted_at)
         WHERE customer_id = NEW.customer_id AND revoked_at IS NULL;
        UPDATE crm.loyalty_card
           SET status = 'closed', closed_at = GREATEST(issued_at, NEW.deleted_at)
         WHERE customer_id = NEW.customer_id AND status <> 'closed';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_customer_soft_delete BEFORE UPDATE OF deleted_at ON crm.customer
    FOR EACH ROW EXECUTE FUNCTION crm.fn_customer_soft_delete();

-- ---------------------------------------------------------------------------
-- Стандартные гарантии
-- ---------------------------------------------------------------------------
SELECT util.add_touch(t::regclass) FROM unnest(ARRAY[
    'crm.customer', 'crm.customer_contact', 'crm.consent', 'crm.loyalty_card']) t;
SELECT util.add_bk_guard('crm.customer', 'customer_bk');
SELECT util.add_bk_guard('crm.loyalty_card', 'card_bk');
SELECT util.add_delete_guard(t::regclass) FROM unnest(ARRAY[
    'crm.customer', 'crm.loyalty_card']) t;
