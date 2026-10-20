-- ============================================================================
-- Контракт crm_service_db: интерфейс для DWH. Только детерминированные выражения
-- (никаких now(), current_date, random()) — отпечатки мастера и реплики обязаны совпасть.
-- ============================================================================
CREATE SCHEMA contract;

CREATE VIEW contract.v_customer AS
SELECT c.customer_bk,
       c.birth_year,
       c.sex,
       c.city,
       c.region,
       c.loyalty_level,
       EXISTS (SELECT 1 FROM crm.consent k
                WHERE k.customer_id = c.customer_id
                  AND k.consent_type = 'marketing'
                  AND k.revoked_at IS NULL)                      AS marketing_opt_in,
       c.registered_at,
       -- изменение согласия тоже двигает updated_at: иначе инкремент его не увидит
       GREATEST(c.updated_at,
                COALESCE((SELECT max(k.updated_at) FROM crm.consent k
                           WHERE k.customer_id = c.customer_id), c.updated_at)) AS updated_at
  FROM crm.customer c
 WHERE c.deleted_at IS NULL;                                    -- удалённые из аналитики исчезают

CREATE VIEW contract.v_loyalty_card AS
SELECT l.card_bk,
       c.customer_bk,
       l.issued_at,
       l.closed_at,
       l.status,
       l.bonus_balance,
       l.updated_at
  FROM crm.loyalty_card l
  JOIN crm.customer c ON c.customer_id = l.customer_id
 WHERE c.deleted_at IS NULL;

GRANT USAGE ON SCHEMA contract TO checker;
GRANT SELECT ON ALL TABLES IN SCHEMA contract TO checker;
