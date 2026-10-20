-- P2: soft delete скрывает клиента из контракта; строка остаётся физически; контакты обезличены.
INSERT INTO crm.customer (customer_bk, full_name, birth_year, sex, city, region, loyalty_level, registered_at)
VALUES ('CUST-990001', 'Тестов Тест Тестович', 1990, 'M', 'Тестоград', 'Тестовый регион', 'basic', TIMESTAMPTZ '2025-01-01 10:00+00');
INSERT INTO crm.customer_contact (customer_id, contact_type, value, is_primary)
SELECT customer_id, 'phone', '+79990000001', true FROM crm.customer WHERE customer_bk = 'CUST-990001';
INSERT INTO crm.customer_contact (customer_id, contact_type, value, is_primary)
SELECT customer_id, 'email', 'test@example.com', false FROM crm.customer WHERE customer_bk = 'CUST-990001';
UPDATE crm.customer SET deleted_at = now() WHERE customer_bk = 'CUST-990001';
SELECT (
        NOT EXISTS (SELECT 1 FROM contract.v_customer WHERE customer_bk = 'CUST-990001')          -- 1. исчез из контракта
    AND EXISTS     (SELECT 1 FROM crm.customer WHERE customer_bk = 'CUST-990001')                  -- 2. строка осталась физически
    AND NOT EXISTS (SELECT 1 FROM crm.customer_contact c JOIN crm.customer u USING (customer_id)
                     WHERE u.customer_bk = 'CUST-990001' AND c.value IS NOT NULL)                  -- 3. контакты обезличены
    AND (SELECT full_name IS NULL FROM crm.customer WHERE customer_bk = 'CUST-990001')
) AS ok;
