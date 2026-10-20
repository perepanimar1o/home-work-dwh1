-- ============================================================================
-- Контракт wms_service_db
-- ============================================================================
CREATE SCHEMA contract;

CREATE VIEW contract.v_supplier AS
SELECT supplier_bk, supplier_name, country, is_active, updated_at
  FROM wms.supplier;

CREATE VIEW contract.v_distribution_center AS
SELECT dc_bk, dc_name, region, city, is_active, updated_at
  FROM wms.distribution_center;

CREATE VIEW contract.v_batch AS
SELECT batch_bk,
       sku_bk AS sku,
       series_no,
       manufactured_date,
       expiry_date,
       received_at,
       updated_at
  FROM wms.batch;

CREATE VIEW contract.v_purchase_line AS
SELECT po.purchase_bk,
       pl.line_no,
       s.supplier_bk,
       dc.dc_bk,
       pl.sku_bk AS sku,
       b.batch_bk,
       po.ordered_at,
       pl.received_at,
       pl.quantity,
       pl.unit_cost,
       pl.line_cost,
       pl.currency::text AS currency,
       pl.updated_at
  FROM wms.purchase_line pl
  JOIN wms.purchase_order po ON po.purchase_id = pl.purchase_id
  JOIN wms.supplier s ON s.supplier_id = po.supplier_id
  JOIN wms.distribution_center dc ON dc.dc_id = po.dc_id
  LEFT JOIN wms.batch b ON b.batch_id = pl.batch_id;

CREATE VIEW contract.v_movement AS
SELECT m.movement_bk,
       b.batch_bk,
       m.sku_bk AS sku,
       m.movement_type,
       m.src_type,
       m.src_bk,
       m.dst_type,
       m.dst_bk,
       m.qty,
       m.dispatched_at,
       m.moved_at,
       m.updated_at
  FROM wms.movement m
  JOIN wms.batch b ON b.batch_id = m.batch_id;

CREATE VIEW contract.v_stock AS
SELECT st.location_type,
       st.location_bk,
       b.sku_bk AS sku,
       b.batch_bk,
       st.qty_on_hand,
       st.updated_at
  FROM wms.stock st
  JOIN wms.batch b ON b.batch_id = st.batch_id;

GRANT USAGE ON SCHEMA contract TO checker;
GRANT SELECT ON ALL TABLES IN SCHEMA contract TO checker;
