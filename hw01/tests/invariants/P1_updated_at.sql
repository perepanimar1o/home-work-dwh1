-- P1: updated_at обновляется автоматически, без явного указания в UPDATE.
INSERT INTO catalog.manufacturer (manufacturer_bk, name, country) VALUES ('MFR-9911', 'Тест-производитель', 'RU');
INSERT INTO catalog.sku (sku_bk, sku_name, manufacturer_id, category, is_rx, pack_qty)
SELECT 'SKU-991001', 'Тестовый препарат', manufacturer_id, 'otc', false, 10
  FROM catalog.manufacturer WHERE manufacturer_bk = 'MFR-9911';
UPDATE catalog.sku SET sku_name = sku_name || ' (изменено)' WHERE sku_bk = 'SKU-991001';
SELECT (updated_at > created_at) AS ok FROM catalog.sku WHERE sku_bk = 'SKU-991001';
