-- FulfillCore 0.7.9.5 additive structured Excel receiving migration.
-- Adds a two-stage draft -> commit workflow for section/pallet/box files.
CREATE TABLE IF NOT EXISTS fc_structured_receipts (
    id VARCHAR(36) PRIMARY KEY,
    client_id VARCHAR(36) NOT NULL,
    warehouse_id VARCHAR(36) NOT NULL,
    name VARCHAR(255) NULL,
    reference VARCHAR(255) NULL,
    source_filename VARCHAR(512) NULL,
    source_sheet VARCHAR(255) NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'READY',
    performed_by VARCHAR(255) NULL,
    section_count INTEGER NOT NULL DEFAULT 0,
    pallet_count INTEGER NOT NULL DEFAULT 0,
    box_count INTEGER NOT NULL DEFAULT 0,
    row_count INTEGER NOT NULL DEFAULT 0,
    product_row_count INTEGER NOT NULL DEFAULT 0,
    zero_product_row_count INTEGER NOT NULL DEFAULT 0,
    empty_box_count INTEGER NOT NULL DEFAULT 0,
    quantity_total NUMERIC(18,3) NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    warning_count INTEGER NOT NULL DEFAULT 0,
    committed_operation_id VARCHAR(36) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    committed_at TIMESTAMPTZ NULL
);
CREATE INDEX IF NOT EXISTS ix_fc_structured_receipts_scope
    ON fc_structured_receipts(client_id, warehouse_id, created_at DESC);

CREATE TABLE IF NOT EXISTS fc_structured_receipt_rows (
    id VARCHAR(36) PRIMARY KEY,
    receipt_id VARCHAR(36) NOT NULL,
    row_number INTEGER NOT NULL,
    section_code VARCHAR(128) NULL,
    pallet_code VARCHAR(128) NULL,
    pallet_limit INTEGER NULL,
    box_code VARCHAR(255) NULL,
    batch_name VARCHAR(255) NULL,
    source_name VARCHAR(255) NULL,
    accepted_at_text TEXT NULL,
    days_text VARCHAR(64) NULL,
    volume_m3_text VARCHAR(64) NULL,
    accrued_rub_text VARCHAR(64) NULL,
    article VARCHAR(255) NULL,
    size_name VARCHAR(255) NULL,
    barcode VARCHAR(255) NULL,
    quantity NUMERIC(18,3) NULL,
    is_empty_box BOOLEAN NOT NULL DEFAULT FALSE,
    parse_error_text TEXT NULL,
    error_text TEXT NULL,
    warning_text TEXT NULL,
    validation_status VARCHAR(32) NOT NULL DEFAULT 'VALID',
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS ix_fc_structured_rows_receipt
    ON fc_structured_receipt_rows(receipt_id, row_number);
CREATE INDEX IF NOT EXISTS ix_fc_structured_rows_box
    ON fc_structured_receipt_rows(receipt_id, box_code);
CREATE INDEX IF NOT EXISTS ix_fc_structured_rows_barcode
    ON fc_structured_receipt_rows(receipt_id, barcode);
