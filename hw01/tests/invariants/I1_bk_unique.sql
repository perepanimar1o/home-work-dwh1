-- I1: два разных SKU не могут иметь один business key (catalog.sku.sku_bk). Ожидается 23505.
INSERT INTO catalog.manufacturer (manufacturer_bk, name, country) VALUES ('MFR-9901', 'Тест-производитель', 'RU');
INSERT INTO catalog.sku (sku_bk, sku_name, manufacturer_id, category, is_rx, pack_qty)
SELECT 'SKU-990001', 'Тестовый препарат', manufacturer_id, 'otc', false, 10
  FROM catalog.manufacturer WHERE manufacturer_bk = 'MFR-9901';
INSERT INTO catalog.sku (sku_bk, sku_name, manufacturer_id, category, is_rx, pack_qty)
SELECT 'SKU-990001', 'Другой препарат с тем же ключом', manufacturer_id, 'otc', false, 20
  FROM catalog.manufacturer WHERE manufacturer_bk = 'MFR-9901';
