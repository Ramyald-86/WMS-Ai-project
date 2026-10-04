-- FulfillCore 0.7.9.1 additive stock-reservation migration.
-- InventoryBalance remains physical stock; this ledger tracks stock committed to picking lists.
CREATE TABLE IF NOT EXISTS fc_stock_reservations (
    id VARCHAR(36) PRIMARY KEY,
    order_id VARCHAR(36) NOT NULL,
    external_order_id VARCHAR(64) NULL,
    picking_list_id VARCHAR(36) NULL,
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    sku_id VARCHAR(36) NOT NULL,
    source_location_id VARCHAR(36) NOT NULL,
    source_container_id VARCHAR(36) NULL,
    quantity NUMERIC(18,3) NOT NULL DEFAULT 1,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    consumed_at TIMESTAMPTZ NULL,
    receiving_at TIMESTAMPTZ NULL,
    released_at TIMESTAMPTZ NULL,
    note TEXT NULL
);
CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_bucket ON fc_stock_reservations(client_id, warehouse_id, sku_id, source_location_id, source_container_id, status);
CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_order ON fc_stock_reservations(order_id, created_at);
CREATE INDEX IF NOT EXISTS ix_fc_stock_reservations_receiving ON fc_stock_reservations(client_id, warehouse_id, sku_id, status, receiving_at);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_stock_reservations_live_order ON fc_stock_reservations(order_id) WHERE status IN ('ACTIVE','CONSUMING','CANCELLING');
