FulfillCore 0.9.0 Stock Core v2

- Physical InventoryBalance remains the address truth.
- fc_stocks_v2 divides it into fbs_available / storage_only / reserved.
- Picking list: FBS_AVAILABLE -> RESERVED, physical quantity does not change.
- Final FBS scanner completion: RESERVED is physically written off exactly once.
- Manual API: reserve -> scan complete -> explicit write-off button.
- Undo write-off restores original category.
- Marketplace changes go to transactional outbox.
- WB v3 outbox uses seller warehouseId plus sku barcode and amount.
- Picking list is SLA sorted: 300+ hours first, then oldest to newest.
- XLSX includes Age hours and SLA 300+ columns.

Migration is additive; old InventoryBalance is not dropped.
