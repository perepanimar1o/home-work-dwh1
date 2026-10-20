-- ============================================================================
-- 000_common.sql — служебные функции, одинаковые во всех четырёх базах.
-- Выполняется первым для каждого сервиса (см. docker/patroni/post_bootstrap.sh).
-- ============================================================================
CREATE SCHEMA IF NOT EXISTS util;

-- updated_at обновляется автоматически, а не руками из приложения.
-- clock_timestamp(), а не now(): два UPDATE в одной транзакции дают разные значения.
CREATE OR REPLACE FUNCTION util.touch_updated_at() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    NEW.updated_at := clock_timestamp();
    RETURN NEW;
END $$;

-- Business key неизменяем (инвариант I2): колонка передаётся первым аргументом триггера.
CREATE OR REPLACE FUNCTION util.forbid_bk_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF to_jsonb(NEW) ->> TG_ARGV[0] IS DISTINCT FROM to_jsonb(OLD) ->> TG_ARGV[0] THEN
        RAISE EXCEPTION 'business key %.%: изменение запрещено (% -> %)',
            TG_TABLE_NAME, TG_ARGV[0], to_jsonb(OLD) ->> TG_ARGV[0], to_jsonb(NEW) ->> TG_ARGV[0]
            USING ERRCODE = 'P0001';
    END IF;
    RETURN NEW;
END $$;

-- Физический DELETE в справочниках запрещён: только soft delete.
CREATE OR REPLACE FUNCTION util.forbid_delete() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION 'физический DELETE из %.% запрещён: используйте soft delete (deleted_at)',
        TG_TABLE_SCHEMA, TG_TABLE_NAME USING ERRCODE = 'P0001';
END $$;

CREATE OR REPLACE FUNCTION util.add_touch(tbl regclass) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('CREATE TRIGGER trg_touch_updated_at BEFORE UPDATE ON %s '
                   'FOR EACH ROW EXECUTE FUNCTION util.touch_updated_at()', tbl);
END $$;

CREATE OR REPLACE FUNCTION util.add_bk_guard(tbl regclass, bk_column text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('CREATE TRIGGER trg_bk_immutable BEFORE UPDATE ON %s '
                   'FOR EACH ROW EXECUTE FUNCTION util.forbid_bk_update(%L)', tbl, bk_column);
END $$;

CREATE OR REPLACE FUNCTION util.add_delete_guard(tbl regclass) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
    EXECUTE format('CREATE TRIGGER trg_no_delete BEFORE DELETE ON %s '
                   'FOR EACH ROW EXECUTE FUNCTION util.forbid_delete()', tbl);
END $$;
