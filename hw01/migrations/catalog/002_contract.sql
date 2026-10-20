-- ============================================================================
-- Контракт catalog_service_db
-- ============================================================================
CREATE SCHEMA contract;

CREATE VIEW contract.v_sku AS
SELECT s.sku_bk                                                    AS sku,
       s.sku_name,
       (SELECT string_agg(i.name, ', ' ORDER BY i.name)
          FROM catalog.sku_inn si
          JOIN catalog.inn i ON i.inn_id = si.inn_id
         WHERE si.sku_id = s.sku_id)                               AS inn_name,
       m.name                                                      AS manufacturer_name,
       f.name                                                      AS drug_form,
       s.category,
       s.is_rx,
       s.is_vital,
       s.pack_qty,
       s.is_active,
       s.updated_at
  FROM catalog.sku s
  JOIN catalog.manufacturer m ON m.manufacturer_id = s.manufacturer_id
  LEFT JOIN catalog.drug_form f ON f.drug_form_id = s.drug_form_id;

CREATE VIEW contract.v_price AS
SELECT s.sku_bk AS sku,
       p.price_scope,
       p.scope_bk,
       p.price,
       p.currency::text AS currency,
       p.valid_from,
       p.valid_to,
       p.is_current,
       p.updated_at
  FROM catalog.price p
  JOIN catalog.sku s ON s.sku_id = p.sku_id;

GRANT USAGE ON SCHEMA contract TO checker;
GRANT SELECT ON ALL TABLES IN SCHEMA contract TO checker;
