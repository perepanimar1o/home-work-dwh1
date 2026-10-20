-- I2: business key неизменяем: UPDATE sku_bk обязан упасть (триггер, P0001).
INSERT INTO catalog.manufacturer (manufacturer_bk, name, country) VALUES ('MFR-9902', 'Тест-производитель', 'RU');
INSERT INTO catalog.sku (sku_bk, sku_name, manufacturer_id, category, is_rx, pack_qty)
SELECT 'SKU-990002', 'Тестовый препарат', manufacturer_id, 'otc', false, 10
  FROM catalog.manufacturer WHERE manufacturer_bk = 'MFR-9902';
UPDATE catalog.sku SET sku_bk = 'SKU-990099' WHERE sku_bk = 'SKU-990002';
