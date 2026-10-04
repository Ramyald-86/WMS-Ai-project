-- FulfillCore 0.7.9.0 additive reliability migration.
-- No existing business table is dropped or rewritten.
CREATE TABLE IF NOT EXISTS fc_runtime_state (
    state_key VARCHAR(255) PRIMARY KEY,
    value_text TEXT NULL,
    updated_epoch BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fc_workstation_profiles (
    station_code VARCHAR(64) PRIMARY KEY,
    client_id VARCHAR(36) NULL,
    warehouse_id VARCHAR(36) NULL,
    printer_profile TEXT NULL,
    scanner_profile TEXT NULL,
    updated_epoch BIGINT NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fc_packing_leases (
    order_id VARCHAR(36) PRIMARY KEY,
    station_code VARCHAR(64) NOT NULL,
    device_id VARCHAR(64) NULL,
    credential_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NULL,
    barcode VARCHAR(255) NULL,
    leased_until_epoch BIGINT NOT NULL,
    updated_epoch BIGINT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fc_packing_leases_credential_barcode ON fc_packing_leases(credential_id, barcode);
CREATE INDEX IF NOT EXISTS ix_fc_packing_leases_until ON fc_packing_leases(leased_until_epoch);
ALTER TABLE fc_packing_leases ADD COLUMN IF NOT EXISTS device_id VARCHAR(64) NULL;
CREATE TABLE IF NOT EXISTS fc_wb_stock_publications (
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    barcode VARCHAR(255) NOT NULL,
    wb_warehouse_id BIGINT NULL,
    published_amount INTEGER NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    last_error TEXT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (client_id, warehouse_id, barcode)
);
CREATE TABLE IF NOT EXISTS fc_pallet_receipts (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    name VARCHAR(255),
    reference VARCHAR(255),
    status VARCHAR(32) NOT NULL DEFAULT 'OPEN',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE TABLE IF NOT EXISTS fc_pallet_receipt_pallets (
    id VARCHAR(36) PRIMARY KEY,
    receipt_id VARCHAR(36) NOT NULL,
    pallet_code VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    closed_at TIMESTAMPTZ NULL,
    UNIQUE(receipt_id, pallet_code)
);
CREATE TABLE IF NOT EXISTS fc_pallet_receipt_boxes (
    id VARCHAR(36) PRIMARY KEY,
    receipt_id VARCHAR(36) NOT NULL,
    pallet_code VARCHAR(255) NOT NULL,
    box_code VARCHAR(255) NOT NULL,
    container_id VARCHAR(36) NULL,
    container_created BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(receipt_id, box_code)
);
ALTER TABLE fc_pallet_receipt_pallets ADD COLUMN IF NOT EXISTS closed_at TIMESTAMPTZ NULL;
ALTER TABLE fc_pallet_receipt_boxes ADD COLUMN IF NOT EXISTS container_id VARCHAR(36) NULL;
ALTER TABLE fc_pallet_receipt_boxes ADD COLUMN IF NOT EXISTS container_created BOOLEAN NOT NULL DEFAULT FALSE;
