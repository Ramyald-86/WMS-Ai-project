CREATE TABLE IF NOT EXISTS fc_stocks_v2 (
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    sku_id VARCHAR(36) NOT NULL,
    location_id VARCHAR(36) NOT NULL,
    container_key VARCHAR(36) NOT NULL DEFAULT '',
    fbs_available NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (fbs_available >= 0),
    storage_only NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (storage_only >= 0),
    reserved NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (reserved >= 0),
    physical_snapshot NUMERIC(18,3) NOT NULL DEFAULT 0 CHECK (physical_snapshot >= 0),
    version BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (client_id, warehouse_id, sku_id, location_id, container_key)
);
CREATE INDEX IF NOT EXISTS ix_fc_stocks_v2_sku ON fc_stocks_v2(client_id, warehouse_id, sku_id);
CREATE INDEX IF NOT EXISTS ix_fc_stocks_v2_fbs ON fc_stocks_v2(client_id, warehouse_id, sku_id) WHERE fbs_available > 0;

CREATE TABLE IF NOT EXISTS fc_orders_v2 (
    id VARCHAR(36) PRIMARY KEY,
    source_order_id VARCHAR(36) NULL,
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    external_order_id VARCHAR(128) NULL,
    order_type VARCHAR(16) NOT NULL CHECK (order_type IN ('FBS_WB','FBS_OZON','MANUAL')),
    status VARCHAR(32) NOT NULL DEFAULT 'NEW' CHECK (status IN ('NEW','RESERVED','SCANNING','READY_WRITE_OFF','WRITTEN_OFF','CANCELLED','RESTORED')),
    created_at TIMESTAMPTZ NOT NULL,
    imported_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    scan_completed_at TIMESTAMPTZ NULL,
    written_off_at TIMESTAMPTZ NULL,
    cancelled_at TIMESTAMPTZ NULL,
    credential_id VARCHAR(36) NULL,
    external_warehouse_id VARCHAR(128) NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_fc_orders_v2_source ON fc_orders_v2(source_order_id) WHERE source_order_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_fc_orders_v2_sla ON fc_orders_v2(client_id, warehouse_id, status, created_at);

CREATE TABLE IF NOT EXISTS fc_order_lines_v2 (
    id VARCHAR(36) PRIMARY KEY,
    order_id VARCHAR(36) NOT NULL REFERENCES fc_orders_v2(id) ON DELETE CASCADE,
    sku_id VARCHAR(36) NOT NULL,
    quantity NUMERIC(18,3) NOT NULL CHECK (quantity > 0),
    source_bucket VARCHAR(24) NULL CHECK (source_bucket IS NULL OR source_bucket IN ('FBS_AVAILABLE','STORAGE_ONLY')),
    reserved_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
    written_off_quantity NUMERIC(18,3) NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_fc_order_lines_v2_order ON fc_order_lines_v2(order_id);

ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS source_bucket VARCHAR(24) NULL;
ALTER TABLE fc_stock_reservations ADD COLUMN IF NOT EXISTS stock_core_order_id VARCHAR(36) NULL;

CREATE TABLE IF NOT EXISTS fc_stock_operations_v2 (
    id VARCHAR(36) PRIMARY KEY,
    order_id VARCHAR(36) NULL,
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    operation_type VARCHAR(40) NOT NULL,
    idempotency_key VARCHAR(255) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_by TEXT NULL,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS fc_marketplace_stock_outbox_v2 (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    sku_id VARCHAR(36) NOT NULL,
    provider VARCHAR(16) NOT NULL CHECK (provider IN ('WB','OZON')),
    credential_id VARCHAR(36) NULL,
    external_warehouse_id VARCHAR(128) NULL,
    product_key VARCHAR(255) NOT NULL,
    desired_amount INTEGER NOT NULL CHECK (desired_amount >= 0),
    reason VARCHAR(80) NOT NULL,
    status VARCHAR(16) NOT NULL DEFAULT 'PENDING' CHECK (status IN ('PENDING','PROCESSING','DONE','FAILED')),
    attempts INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_error TEXT NULL,
    dedupe_key VARCHAR(512) NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_fc_marketplace_stock_outbox_v2_pending ON fc_marketplace_stock_outbox_v2(status,next_attempt_at);

CREATE OR REPLACE VIEW fc_stock_totals_v2 AS
SELECT client_id, warehouse_id, sku_id,
       SUM(fbs_available) AS fbs_available,
       SUM(storage_only) AS storage_only,
       SUM(reserved) AS reserved,
       SUM(fbs_available + storage_only + reserved) AS physical_total
FROM fc_stocks_v2
GROUP BY client_id, warehouse_id, sku_id;
