-- I7: остаток не может стать отрицательным: приёмка 10, списание 11 -> CHECK stock_not_negative (23514).
INSERT INTO wms.supplier (supplier_bk, supplier_name, country) VALUES ('SUP-9907', 'Тест-поставщик', 'RU');
INSERT INTO wms.distribution_center (dc_bk, dc_name, region, city) VALUES ('DC-97', 'Тест-РЦ', 'Тестовый регион', 'Тестоград');
INSERT INTO wms.batch (batch_bk, sku_bk, series_no, manufactured_date, expiry_date, received_at)
VALUES ('BATCH-2099-00007', 'SKU-990007', 'S-9907', DATE '2025-06-01', DATE '2030-06-01', TIMESTAMPTZ '2026-01-01 10:00+00');
INSERT INTO wms.movement (movement_bk, batch_id, sku_bk, movement_type, src_type, src_bk, dst_type, dst_bk, qty, dispatched_at, moved_at)
SELECT 'MOV-990000001', batch_id, sku_bk, 'receipt', 'supplier', 'SUP-9907', 'dc', 'DC-97', 10,
       TIMESTAMPTZ '2026-01-01 09:00+00', TIMESTAMPTZ '2026-01-01 10:00+00'
  FROM wms.batch WHERE batch_bk = 'BATCH-2099-00007';
INSERT INTO wms.movement (movement_bk, batch_id, sku_bk, movement_type, src_type, src_bk, dst_type, dst_bk, qty, moved_at)
SELECT 'MOV-990000002', batch_id, sku_bk, 'writeoff', 'dc', 'DC-97', NULL, NULL, 11, TIMESTAMPTZ '2026-01-02 10:00+00'
  FROM wms.batch WHERE batch_bk = 'BATCH-2099-00007';
