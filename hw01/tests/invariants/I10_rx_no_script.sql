-- I10: рецептурный препарат не отпускается без рецепта.
-- Признак живёт в каталоге, поэтому POS хранит sku_ref + snapshot в позиции; проверка отложенная (P0001 на COMMIT).
INSERT INTO pos.pharmacy (pharmacy_bk, pharmacy_name, region, city, address, opened_at)
VALUES ('PH-9901', 'Тестовая аптека', 'Тестовый регион', 'Тестоград', 'ул. Тестовая, 1', DATE '2020-01-01');
INSERT INTO pos.register (register_bk, pharmacy_id)
SELECT 'REG-9901-01', pharmacy_id FROM pos.pharmacy WHERE pharmacy_bk = 'PH-9901';
INSERT INTO pos.cashier (cashier_bk, full_name, pharmacy_id)
SELECT 'CSH-990001', 'Тестов Тест Тестович', pharmacy_id FROM pos.pharmacy WHERE pharmacy_bk = 'PH-9901';
INSERT INTO pos.shift (shift_bk, register_id, cashier_id, opened_at, closed_at)
SELECT 'SHF-20260101-9901-01', r.register_id, c.cashier_id,
       TIMESTAMPTZ '2026-01-01 08:00+00', TIMESTAMPTZ '2026-01-01 22:00+00'
  FROM pos.register r, pos.cashier c WHERE r.register_bk = 'REG-9901-01' AND c.cashier_bk = 'CSH-990001';
INSERT INTO pos.sku_ref (sku_bk, sku_name, is_rx, is_rx_since)
VALUES ('SKU-990001', 'Тестовый препарат', false, NULL),
       ('SKU-990002', 'Тестовый рецептурный препарат', true, TIMESTAMPTZ '2020-01-01 00:00+00');
INSERT INTO pos.batch_ref (batch_bk, sku_bk, expiry_date)
VALUES ('BATCH-2099-00001', 'SKU-990001', DATE '2030-01-01'),
       ('BATCH-2099-00002', 'SKU-990002', DATE '2030-01-01'),
       ('BATCH-2099-00003', 'SKU-990001', DATE '2025-12-31');
INSERT INTO pos.receipt (receipt_bk, pharmacy_id, shift_id, receipt_dt, doc_type, total_amount, discount_amount, prescription_id)
SELECT 'RCPT-20260101-990010', p.pharmacy_id, s.shift_id, TIMESTAMPTZ '2026-01-01 12:00+00', 'sale', 100, 0, NULL
  FROM pos.pharmacy p, pos.shift s WHERE p.pharmacy_bk = 'PH-9901' AND s.shift_bk = 'SHF-20260101-9901-01';
INSERT INTO pos.receipt_line (receipt_id, line_no, sku_bk, batch_bk, quantity, unit_price, line_discount, is_rx_snapshot, sku_name_snapshot)
SELECT receipt_id, 1, 'SKU-990002', 'BATCH-2099-00002', 1, 100, 0, true, 'Тестовый рецептурный препарат'
  FROM pos.receipt WHERE receipt_bk = 'RCPT-20260101-990010';
INSERT INTO pos.payment (receipt_id, payment_no, payment_method, amount)
SELECT receipt_id, 1, 'card', 100 FROM pos.receipt WHERE receipt_bk = 'RCPT-20260101-990010';
