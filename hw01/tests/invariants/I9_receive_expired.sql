-- I9: на склад не принимается партия, срок годности которой истёк к моменту приёмки (CHECK, 23514).
INSERT INTO wms.batch (batch_bk, sku_bk, series_no, manufactured_date, expiry_date, received_at)
VALUES ('BATCH-2099-00009', 'SKU-990009', 'S-9909', DATE '2024-01-01', DATE '2025-12-31', TIMESTAMPTZ '2026-01-01 10:00+00');
