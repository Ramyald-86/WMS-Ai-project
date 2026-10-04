import re
import uuid
from collections import defaultdict
from decimal import Decimal

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.models import Client, Container, ImportBatch, InventoryBalance, Location, SKU, Warehouse
from app.schemas.inventory import OperationCreate, StockLine
from app.services.excel_import import parse_workbook
from app.services.inventory import execute_operation

router = APIRouter(prefix="/imports", tags=["Массовый импорт"])


def _slug(value: str) -> str:
    value = re.sub(r"[^A-Za-zА-Яа-я0-9._-]+", "-", value.strip())
    return value[:100].strip("-") or "SKU"


def _get_or_create_location(db: Session, warehouse_id: uuid.UUID, code: str) -> tuple[Location, bool]:
    loc = db.execute(
        select(Location).where(
            Location.warehouse_id == warehouse_id,
            or_(Location.code == code, Location.barcode == code),
        )
    ).scalars().first()
    if loc:
        return loc, False
    loc = Location(
        warehouse_id=warehouse_id,
        parent_id=None,
        code=code,
        barcode=code,
        name=f"Ячейка {code}",
        location_type="CELL",
        is_active=True,
    )
    db.add(loc)
    db.flush()
    return loc, True


def _resolve_sku(db: Session, client_id: uuid.UUID, row: dict) -> tuple[SKU, bool]:
    barcode = row.get("barcode")
    article = row.get("article")
    size = row.get("size")
    sku_code = row.get("sku_code")

    sku = None
    if barcode:
        sku = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.barcode == barcode)).scalars().first()
    if not sku and sku_code:
        sku = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.sku_code == sku_code)).scalars().first()
    if not sku and article:
        stmt = select(SKU).where(SKU.client_id == client_id, SKU.article == article)
        stmt = stmt.where(SKU.size == size) if size is not None else stmt.where(SKU.size.is_(None))
        sku = db.execute(stmt).scalars().first()
    if sku:
        return sku, False

    identity = sku_code or barcode or "-".join(x for x in [article, size] if x) or f"ROW-{row['row']}"
    code = _slug(identity)
    existing = db.execute(select(SKU).where(SKU.client_id == client_id, SKU.sku_code == code)).scalars().first()
    if existing:
        code = f"{code}-{str(uuid.uuid4())[:8]}"
    sku = SKU(
        client_id=client_id,
        sku_code=code,
        article=article,
        name=row.get("name") or article or sku_code or barcode or code,
        size=size,
        barcode=barcode,
        unit="PCS",
        is_active=True,
    )
    db.add(sku)
    db.flush()
    return sku, True


def _resolve_container(db: Session, client_id: uuid.UUID, warehouse_id: uuid.UUID, box: str, location: Location) -> tuple[Container, bool]:
    container = db.execute(
        select(Container).where(
            Container.client_id == client_id,
            Container.warehouse_id == warehouse_id,
            or_(Container.barcode == box, Container.name == box),
        )
    ).scalars().first()
    if container:
        if container.location_id is None:
            container.location_id = location.id
        elif container.location_id != location.id:
            positive = db.execute(
                select(func.count(InventoryBalance.id)).where(
                    InventoryBalance.container_id == container.id,
                    InventoryBalance.quantity > 0,
                )
            ).scalar_one()
            if positive:
                current = db.get(Location, container.location_id)
                raise HTTPException(
                    status_code=409,
                    detail=f"Короб '{box}' уже находится в ячейке {current.code if current else container.location_id}. Сначала переместите его штатной операцией.",
                )
            container.location_id = location.id
        return container, False

    # При первом импорте название короба одновременно используется как его сканируемый код.
    container = Container(
        client_id=client_id,
        warehouse_id=warehouse_id,
        location_id=location.id,
        barcode=box,
        name=box,
        container_type="BOX",
        status="ACTIVE",
    )
    db.add(container)
    db.flush()
    return container, True


@router.post("/excel/preview")
async def preview_excel(file: UploadFile = File(...)):
    raw = await file.read()
    parsed = parse_workbook(raw, file.filename)
    invalid = []
    valid = 0
    for row in parsed.rows:
        errors = []
        if not row.get("box"):
            errors.append("нет короба")
        if not row.get("quantity"):
            errors.append("нет/неверное количество")
        if not any(row.get(k) for k in ("barcode", "article", "sku_code")):
            errors.append("нет ШК/артикула/SKU")
        if errors:
            invalid.append({"row": row["row"], "errors": errors})
        else:
            valid += 1
    return {
        "file_name": file.filename,
        "sheet": parsed.sheet_name,
        "header_row": parsed.header_row,
        "mapping": {k: parsed.headers[v] for k, v in parsed.mapping.items()},
        "rows_total": len(parsed.rows),
        "rows_valid": valid,
        "rows_invalid": len(invalid),
        "invalid": invalid[:50],
        "sample": [
            {**r, "quantity": float(r["quantity"]) if r.get("quantity") else None}
            for r in parsed.rows[:20]
        ],
        "checksum": parsed.checksum,
    }


@router.post("/excel/execute")
async def execute_excel_import(
    client_id: uuid.UUID = Form(...),
    warehouse_id: uuid.UUID = Form(...),
    default_location_code: str = Form("ПРИЕМКА"),
    performed_by: str | None = Form(None),
    force: bool = Form(False),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not db.get(Client, client_id):
        raise HTTPException(status_code=404, detail="Клиент не найден")
    if not db.get(Warehouse, warehouse_id):
        raise HTTPException(status_code=404, detail="Склад не найден")

    raw = await file.read()
    parsed = parse_workbook(raw, file.filename)
    if not force:
        duplicate = db.execute(
            select(ImportBatch).where(
                ImportBatch.client_id == client_id,
                ImportBatch.warehouse_id == warehouse_id,
                ImportBatch.checksum == parsed.checksum,
                ImportBatch.status == "COMPLETED",
            )
        ).scalars().first()
        if duplicate:
            raise HTTPException(status_code=409, detail=f"Этот файл уже импортирован: {duplicate.file_name} ({duplicate.created_at})")

    invalid = []
    for row in parsed.rows:
        errors = []
        if not row.get("box"):
            errors.append("нет короба")
        if not row.get("quantity"):
            errors.append("нет/неверное количество")
        if not any(row.get(k) for k in ("barcode", "article", "sku_code")):
            errors.append("нет ШК/артикула/SKU")
        if errors:
            invalid.append({"row": row["row"], "errors": errors})
    if invalid:
        raise HTTPException(status_code=400, detail={"message": "В файле есть ошибки. Сначала исправьте их.", "rows": invalid[:100]})

    created_skus = created_containers = created_locations = 0
    # Суммируем одинаковые позиции, чтобы большой Excel создавал компактный журнал.
    aggregate: dict[tuple[uuid.UUID, uuid.UUID, uuid.UUID], Decimal] = defaultdict(lambda: Decimal("0"))

    try:
        for row in parsed.rows:
            loc_code = row.get("location") or default_location_code.strip() or "ПРИЕМКА"
            location, is_new_loc = _get_or_create_location(db, warehouse_id, loc_code)
            created_locations += int(is_new_loc)
            sku, is_new_sku = _resolve_sku(db, client_id, row)
            created_skus += int(is_new_sku)
            container, is_new_container = _resolve_container(db, client_id, warehouse_id, row["box"], location)
            created_containers += int(is_new_container)
            aggregate[(sku.id, location.id, container.id)] += row["quantity"]

        lines = [
            StockLine(
                sku_id=sku_id,
                quantity=quantity,
                destination_location_id=location_id,
                destination_container_id=container_id,
                reason="EXCEL_IMPORT",
            )
            for (sku_id, location_id, container_id), quantity in aggregate.items()
        ]
        if not lines:
            raise HTTPException(status_code=400, detail="В файле нет строк для импорта")

        op = execute_operation(
            db,
            OperationCreate(
                client_id=client_id,
                warehouse_id=warehouse_id,
                operation_type="RECEIVING_IMPORT",
                idempotency_key=f"excel:{parsed.checksum}:{uuid.uuid4() if force else 'v1'}",
                performed_by=performed_by,
                reference=file.filename,
                comment=f"Массовая загрузка Excel, лист {parsed.sheet_name}",
                lines=lines,
            ),
            commit=False,
        )
        batch = ImportBatch(
            client_id=client_id,
            warehouse_id=warehouse_id,
            file_name=file.filename or "import.xlsx",
            checksum=parsed.checksum,
            status="COMPLETED",
            rows_total=len(parsed.rows),
            rows_imported=len(parsed.rows),
            rows_skipped=0,
            created_skus=created_skus,
            created_containers=created_containers,
            created_locations=created_locations,
            operation_id=op.id,
            performed_by=performed_by,
            note=f"Агрегировано в {len(lines)} складских строк",
        )
        db.add(batch)
        db.commit()
        db.refresh(batch)
    except HTTPException:
        db.rollback()
        raise
    except Exception as exc:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Импорт не выполнен: {exc}") from exc

    return {
        "batch_id": str(batch.id),
        "operation_id": str(op.id),
        "rows_imported": len(parsed.rows),
        "stock_lines": len(lines),
        "created_skus": created_skus,
        "created_containers": created_containers,
        "created_locations": created_locations,
        "message": "Импорт завершён. Остатки созданы через складскую операцию RECEIVING_IMPORT.",
    }
