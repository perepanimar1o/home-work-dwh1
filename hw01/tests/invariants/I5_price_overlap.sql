-- I5: интервалы действия цены одной области не пересекаются: EXCLUDE USING gist (23P01).
INSERT INTO catalog.manufacturer (manufacturer_bk, name, country) VALUES ('MFR-9905', 'Тест-производитель', 'RU');
INSERT INTO catalog.sku (sku_bk, sku_name, manufacturer_id, category, is_rx, pack_qty)
SELECT 'SKU-990005', 'Тестовый препарат', manufacturer_id, 'otc', false, 10
  FROM catalog.manufacturer WHERE manufacturer_bk = 'MFR-9905';
INSERT INTO catalog.price (sku_id, price_scope, scope_bk, price, valid_from, valid_to, is_current)
SELECT sku_id, 'chain', 'ALL', 100, TIMESTAMPTZ '2099-01-01 00:00+00', TIMESTAMPTZ '2099-06-01 00:00+00', false
  FROM catalog.sku WHERE sku_bk = 'SKU-990005';
-- второй интервал начинается внутри первого
INSERT INTO catalog.price (sku_id, price_scope, scope_bk, price, valid_from, valid_to, is_current)
SELECT sku_id, 'chain', 'ALL', 110, TIMESTAMPTZ '2099-03-01 00:00+00', TIMESTAMPTZ '2099-09-01 00:00+00', false
  FROM catalog.sku WHERE sku_bk = 'SKU-990005';
