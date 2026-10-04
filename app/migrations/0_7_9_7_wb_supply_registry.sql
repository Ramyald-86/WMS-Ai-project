CREATE TABLE IF NOT EXISTS fc_wb_supply_registry (
    client_id VARCHAR(36) NOT NULL,
    cabinet_name VARCHAR(255) NOT NULL DEFAULT '',
    supply_id VARCHAR(64) NOT NULL,
    credential_id VARCHAR(36) NULL,
    physical_warehouse_id VARCHAR(36) NULL,
    wb_warehouse_id BIGINT NULL,
    supply_name VARCHAR(255) NULL,
    done BOOLEAN NULL DEFAULT FALSE,
    order_count INTEGER NULL DEFAULT 0,
    source VARCHAR(32) NULL DEFAULT 'WB',
    raw_json TEXT NULL,
    last_error TEXT NULL,
    created_at_text TEXT NULL,
    closed_at_text TEXT NULL,
    last_live_sync_epoch BIGINT NOT NULL DEFAULT 0,
    updated_epoch BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (client_id, cabinet_name, supply_id)
);
CREATE INDEX IF NOT EXISTS ix_fc_wb_supply_registry_scope
    ON fc_wb_supply_registry(client_id, cabinet_name, physical_warehouse_id, done, updated_epoch DESC);
CREATE INDEX IF NOT EXISTS ix_fc_wb_supply_registry_wb_wh
    ON fc_wb_supply_registry(client_id, cabinet_name, wb_warehouse_id, done);
