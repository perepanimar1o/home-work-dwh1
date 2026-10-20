-- I6: ровно одна текущая версия цены: partial UNIQUE INDEX ... WHERE is_current (23505).
-- Интервалы НЕ пересекаются (смежные), чтобы упасть именно на индексе, а не на EXCLUDE.
INSERT INTO catalog.manufacturer (manufacturer_bk, name, country) VALUES ('MFR-9906', 'Тест-производитель', 'RU');
INSERT INTO catalog.sku (sku_bk, sku_name, manufacturer_id, category, is_rx, pack_qty)
SELECT 'SKU-990006', 'Тестовый препарат', manufacturer_id, 'otc', false, 10
  FROM catalog.manufacturer WHERE manufacturer_bk = 'MFR-9906';
INSERT INTO catalog.price (sku_id, price_scope, scope_bk, price, valid_from, valid_to, is_current)
SELECT sku_id, 'chain', 'ALL', 100, TIMESTAMPTZ '2099-01-01 00:00+00', TIMESTAMPTZ '2099-06-01 00:00+00', true
  FROM catalog.sku WHERE sku_bk = 'SKU-990006';
INSERT INTO catalog.price (sku_id, price_scope, scope_bk, price, valid_from, valid_to, is_current)
SELECT sku_id, 'chain', 'ALL', 110, TIMESTAMPTZ '2099-06-01 00:00+00', TIMESTAMPTZ '2099-12-01 00:00+00', true
  FROM catalog.sku WHERE sku_bk = 'SKU-990006';
