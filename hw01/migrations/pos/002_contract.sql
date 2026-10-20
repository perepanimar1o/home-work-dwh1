-- ============================================================================
-- Контракт pos_service_db
-- ============================================================================
CREATE SCHEMA contract;

CREATE VIEW contract.v_pharmacy AS
SELECT p.pharmacy_bk, p.pharmacy_name, p.region, p.city, p.address,
       p.opened_at, p.closed_at, p.is_active, p.updated_at
  FROM pos.pharmacy p;

CREATE VIEW contract.v_receipt AS
SELECT r.receipt_bk,
       ph.pharmacy_bk,
       r.customer_bk,
       c.cashier_bk,
       s.shift_bk,
       r.receipt_dt,
       r.doc_type,
       parent.receipt_bk       AS parent_receipt_bk,
       r.total_amount,
       r.discount_amount,
       r.currency::text        AS currency,
       rx.prescription_bk,
       r.updated_at
  FROM pos.receipt r
  JOIN pos.pharmacy ph ON ph.pharmacy_id = r.pharmacy_id
  JOIN pos.shift s     ON s.shift_id = r.shift_id
  JOIN pos.cashier c   ON c.cashier_id = s.cashier_id
  LEFT JOIN pos.receipt parent       ON parent.receipt_id = r.parent_receipt_id
  LEFT JOIN pos.prescription rx      ON rx.prescription_id = r.prescription_id;

CREATE VIEW contract.v_receipt_line AS
SELECT r.receipt_bk,
       l.line_no,
       l.sku_bk            AS sku,
       l.batch_bk,
       l.quantity,
       l.unit_price,
       l.line_discount,
       l.line_amount,
       l.is_rx_snapshot,
       l.sku_name_snapshot,
       l.updated_at
  FROM pos.receipt_line l
  JOIN pos.receipt r ON r.receipt_id = l.receipt_id;

CREATE VIEW contract.v_payment AS
SELECT r.receipt_bk,
       p.payment_no,
       p.payment_method,
       p.amount,
       p.currency::text    AS currency,
       p.updated_at
  FROM pos.payment p
  JOIN pos.receipt r ON r.receipt_id = p.receipt_id;

GRANT USAGE ON SCHEMA contract TO checker;
GRANT SELECT ON ALL TABLES IN SCHEMA contract TO checker;
