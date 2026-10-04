import re
import uuid
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import bindparam, func, or_, select, text
from sqlalchemy.orm import Session

from reportlab.graphics.barcode import createBarcodeDrawing
from reportlab.graphics.barcode.qr import QrCodeWidget
from PIL import Image, ImageDraw, ImageFont
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

from app.core.database import get_db
from app.models import Container, InventoryBalance, InventoryOperation, InventoryOperationLine, Location, MarketplaceProduct, SKU
from app.schemas.inventory import OperationCreate, StockLine
from app.services.inventory import execute_operation
from app.services.printing import queue_print_job
from app.services.product_identity import resolved_sku_identity, wb_product_identity_map
from app.services.stock_core_v2 import stock_truth_for_sku, stock_truth_for_skus
from app.services.stock_reservations import active_reserved_totals
from app.services.container_lifecycle import archive_consumed_empty_containers

router = APIRouter(prefix="/layout", tags=["Адресное хранение"])

SYSTEM_LOCATION_CODES = {"БЕЗ-АДРЕСА", "ПРИЕМКА", "ПРИЕМКА-ПАЛЕТЫ", "UNPLACED"}
_LOCATION_LABEL_FONTS_READY = False


class LocationLayoutCreate(BaseModel):
    warehouse_id: uuid.UUID
    kind: Literal["RACK", "CELL", "PALLET", "PALLET_PLACE", "SECTION"]
    # Optional parent section for cells/racks/pallets/pallet places. SECTION itself
    # uses the same field as a free-form single section name.
    section: str | None = Field(default=None, max_length=80)
    section_from: int | None = Field(default=None, ge=1, le=99999)
    section_to: int | None = Field(default=None, ge=1, le=99999)
    aisle: str | None = Field(default=None, max_length=40)
    rack: str | None = Field(default=None, max_length=60)
    floors: int | None = Field(default=None, ge=1, le=30)
    cells_per_floor: int | None = Field(default=None, ge=1, le=250)
    floor_from: int | None = Field(default=None, ge=1, le=30)
    floor_to: int | None = Field(default=None, ge=1, le=30)
    cell_from: int | None = Field(default=None, ge=1, le=99999)
    cell_to: int | None = Field(default=None, ge=1, le=99999)
    pallet_number: str | None = Field(default=None, max_length=60)
    pallet_from: int | None = Field(default=None, ge=1, le=99999)
    pallet_to: int | None = Field(default=None, ge=1, le=99999)
    pallet_place_number: str | None = Field(default=None, max_length=60)
    pallet_place_from: int | None = Field(default=None, ge=1, le=99999)
    pallet_place_to: int | None = Field(default=None, ge=1, le=99999)


class BulkArchiveRequest(BaseModel):
    ids: list[uuid.UUID] = Field(min_length=1, max_length=2000)


class LocationLabelQueueRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    print_station: str = Field(min_length=1, max_length=64)
    location_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    code_from: str | None = Field(default=None, max_length=255)
    code_to: str | None = Field(default=None, max_length=255)
    all_locations: bool = False
    width_mm: float = Field(default=58, ge=25, le=120)
    height_mm: float = Field(default=40, ge=25, le=120)


class LocationLabelPdfRequest(BaseModel):
    warehouse_id: uuid.UUID
    location_ids: list[uuid.UUID] = Field(default_factory=list, max_length=2000)
    code_from: str | None = Field(default=None, max_length=255)
    code_to: str | None = Field(default=None, max_length=255)
    all_locations: bool = False
    width_mm: float = Field(default=58, ge=25, le=120)
    height_mm: float = Field(default=40, ge=25, le=120)


class LocationStockRemoveRequest(BaseModel):
    client_id: uuid.UUID
    warehouse_id: uuid.UUID
    sku_id: uuid.UUID
    container_id: uuid.UUID | None = None
    quantity: Decimal = Field(gt=0)
    performed_by: str | None = Field(default=None, max_length=255)


def _code_token(value: str | None, field_name: str) -> str:
    token = re.sub(r"\s+", "-", str(value or "").strip().upper())
    token = re.sub(r"[^0-9A-ZА-ЯЁ_-]", "", token)
    token = re.sub(r"-{2,}", "-", token).strip("-_")
    if not token:
        raise HTTPException(status_code=400, detail=f"Заполните поле «{field_name}»")
    return token


def _location_barcode(warehouse_id: uuid.UUID, code: str) -> str:
    """ASCII-only stable Code 128 value, including when the visible address is Cyrillic."""
    digest = uuid.uuid5(uuid.NAMESPACE_URL, f"fulfillcore:{warehouse_id}:{code}").hex[:16].upper()
    return f"FCLOC-{digest}"


@router.post("/locations/bulk")
def create_location_layout(payload: LocationLayoutCreate, db: Session = Depends(get_db)):
    """Create flexible warehouse addressing without forcing one storage model.

    Existing RACK/PALLET requests remain backward compatible. New warehouses may use
    direct cells, fixed pallet places and explicit sections. When a child address is
    created with ``section``, the section node is created/reused automatically and is
    stored as the parent while the child's readable name also contains the section.
    """
    aisle_raw = str(payload.aisle or "").strip()
    aisle = _code_token(aisle_raw, "Проход") if aisle_raw else ""
    section_raw = str(payload.section or "").strip()

    def section_spec(raw: str) -> dict[str, str | None]:
        token = _code_token(raw, "Секция")
        code = f"SEC-{token}"
        return {
            "code": code,
            "barcode": _location_barcode(payload.warehouse_id, code),
            "name": f"Секция {raw}",
            "location_type": "SECTION",
            "parent_code": None,
        }

    auto_section_spec = section_spec(section_raw) if section_raw and payload.kind != "SECTION" else None
    parent_code = str(auto_section_spec["code"]) if auto_section_spec else None
    section_prefix = parent_code or ""
    specs: list[dict[str, str | None]] = []

    if payload.kind == "SECTION":
        section_values: list[str] = []
        if payload.section_from is not None or payload.section_to is not None:
            start_value = int(payload.section_from or payload.section_to or 0)
            end_value = int(payload.section_to or start_value)
            if end_value < start_value:
                raise HTTPException(status_code=400, detail="Конечная секция не может быть меньше начальной")
            if end_value - start_value + 1 > 1000:
                raise HTTPException(status_code=400, detail="За один раз можно создать не более 1000 секций")
            section_values = [str(value) for value in range(start_value, end_value + 1)]
        elif section_raw:
            section_values = [section_raw]
        else:
            raise HTTPException(status_code=400, detail="Укажите название секции или диапазон секций")
        specs = [section_spec(value) for value in section_values]

    elif payload.kind == "RACK":
        rack_raw = str(payload.rack or "").strip()
        rack = _code_token(rack_raw, "Стеллаж")
        floor_from = int(payload.floor_from or 1)
        floor_to = int(payload.floor_to or payload.floors or floor_from)
        cell_from = int(payload.cell_from or 1)
        cell_to = int(payload.cell_to or payload.cells_per_floor or cell_from)
        if floor_to < floor_from:
            raise HTTPException(status_code=400, detail="Конечный этаж не может быть меньше начального")
        if cell_to < cell_from:
            raise HTTPException(status_code=400, detail="Конечная ячейка не может быть меньше начальной")
        total = (floor_to - floor_from + 1) * (cell_to - cell_from + 1)
        if total > 2000:
            raise HTTPException(status_code=400, detail="За один раз можно создать не более 2000 ячеек")
        for floor in range(floor_from, floor_to + 1):
            for cell in range(cell_from, cell_to + 1):
                code = "-".join(x for x in (section_prefix, aisle, rack, f"{floor:02d}", f"{cell:02d}") if x)
                name_parts = ([f"Секция {section_raw}"] if section_raw else [])
                if aisle_raw:
                    name_parts.append(f"Проход {aisle_raw}")
                name_parts.extend((f"Стеллаж {rack_raw}", f"Этаж {floor}", f"Ячейка {cell}"))
                specs.append({
                    "code": code,
                    "barcode": _location_barcode(payload.warehouse_id, code),
                    "name": " · ".join(name_parts),
                    "location_type": "CELL",
                    "parent_code": parent_code,
                })

    elif payload.kind == "CELL":
        cell_from = int(payload.cell_from or 1)
        cell_to = int(payload.cell_to or cell_from)
        if cell_to < cell_from:
            raise HTTPException(status_code=400, detail="Конечная ячейка не может быть меньше начальной")
        if cell_to - cell_from + 1 > 2000:
            raise HTTPException(status_code=400, detail="За один раз можно создать не более 2000 ячеек")
        width = max(2, len(str(cell_to)))
        for cell in range(cell_from, cell_to + 1):
            cell_value = str(cell).zfill(width)
            code = "-".join(x for x in (section_prefix, aisle, "CELL", cell_value) if x)
            name_parts = ([f"Секция {section_raw}"] if section_raw else [])
            if aisle_raw:
                name_parts.append(f"Проход {aisle_raw}")
            name_parts.append(f"Ячейка {cell}")
            specs.append({
                "code": code,
                "barcode": _location_barcode(payload.warehouse_id, code),
                "name": " · ".join(name_parts),
                "location_type": "CELL",
                "parent_code": parent_code,
            })

    elif payload.kind in {"PALLET", "PALLET_PLACE"}:
        is_place = payload.kind == "PALLET_PLACE"
        number_value = payload.pallet_place_number if is_place else payload.pallet_number
        range_from = payload.pallet_place_from if is_place else payload.pallet_from
        range_to = payload.pallet_place_to if is_place else payload.pallet_to
        label = "Палеточное место" if is_place else "Палета"
        marker = "PP" if is_place else "PAL"
        values: list[str]
        if range_from is not None or range_to is not None:
            start_value = int(range_from or range_to or 0)
            end_value = int(range_to or start_value)
            if end_value < start_value:
                raise HTTPException(status_code=400, detail=f"Конечный номер {label.lower()} не может быть меньше начального")
            if end_value - start_value + 1 > 1000:
                raise HTTPException(status_code=400, detail=f"За один раз можно создать не более 1000 адресов типа «{label}»")
            width = max(3, len(str(end_value)))
            values = [str(value).zfill(width) for value in range(start_value, end_value + 1)]
        else:
            values = [_code_token(number_value, f"Номер: {label}")]
        for value in values:
            code = "-".join(x for x in (section_prefix, aisle, marker, value) if x)
            name_parts = ([f"Секция {section_raw}"] if section_raw else [])
            if aisle_raw:
                name_parts.append(f"Проход {aisle_raw}")
            name_parts.append(f"{label} {value}")
            specs.append({
                "code": code,
                "barcode": _location_barcode(payload.warehouse_id, code),
                "name": " · ".join(name_parts),
                "location_type": "PALLET_PLACE" if is_place else "PALLET",
                "parent_code": parent_code,
            })
    else:
        raise HTTPException(status_code=400, detail="Неизвестный тип адреса")

    all_specs = ([auto_section_spec] if auto_section_spec else []) + specs
    codes = [str(item["code"]) for item in all_specs]
    existing_rows = db.execute(
        select(Location).where(
            Location.warehouse_id == payload.warehouse_id,
            or_(Location.code.in_(codes), Location.barcode.in_(codes)),
        )
    ).scalars().all()
    existing: dict[str, Location] = {}
    for row in existing_rows:
        existing[str(row.code or "")] = row
        existing[str(row.barcode or "")] = row

    section_locations: dict[str, Location] = {}
    created_sections: list[dict[str, str]] = []
    if auto_section_spec:
        section_code = str(auto_section_spec["code"])
        section = existing.get(section_code) or existing.get(str(auto_section_spec["barcode"]))
        if section and str(section.location_type or "").upper() != "SECTION":
            raise HTTPException(status_code=409, detail=f"Код {section_code} уже занят адресом типа {section.location_type or 'UNKNOWN'}")
        if section:
            section.is_active = True
            section.name = str(auto_section_spec["name"])
            if not section.barcode:
                section.barcode = str(auto_section_spec["barcode"])
        else:
            section = Location(
                warehouse_id=payload.warehouse_id,
                parent_id=None,
                code=section_code,
                barcode=str(auto_section_spec["barcode"]),
                name=str(auto_section_spec["name"]),
                location_type="SECTION",
                is_active=True,
            )
            db.add(section)
            db.flush()
            existing[section_code] = section
            created_sections.append({
                "id": str(section.id), "code": section.code, "barcode": section.barcode,
                "name": section.name, "location_type": section.location_type,
            })
        section_locations[section_code] = section

    created: list[dict[str, str]] = []
    skipped: list[str] = []
    reactivated = 0
    for item in specs:
        code = str(item["code"])
        barcode = str(item["barcode"])
        prior = existing.get(code) or existing.get(barcode)
        expected_type = str(item["location_type"])
        parent = section_locations.get(str(item.get("parent_code") or ""))
        if prior:
            if str(prior.location_type or "").upper() != expected_type.upper():
                raise HTTPException(status_code=409, detail=f"Адрес {code} уже существует как {prior.location_type or 'другой тип'}")
            prior.parent_id = parent.id if parent else prior.parent_id
            prior.name = str(item["name"])
            if not prior.barcode:
                prior.barcode = barcode
            if prior.is_active:
                skipped.append(code)
                continue
            prior.is_active = True
            reactivated += 1
            created.append({
                "id": str(prior.id), "code": prior.code, "barcode": prior.barcode,
                "name": prior.name, "location_type": prior.location_type,
            })
            continue
        location = Location(
            warehouse_id=payload.warehouse_id,
            parent_id=parent.id if parent else None,
            code=code,
            barcode=barcode,
            name=str(item["name"]),
            location_type=expected_type,
            is_active=True,
        )
        db.add(location)
        db.flush()
        existing[code] = location
        created.append({
            "id": str(location.id), "code": location.code, "barcode": location.barcode,
            "name": location.name, "location_type": location.location_type,
        })

    # SECTION creation uses specs themselves, so expose them as ordinary created rows.
    if payload.kind == "SECTION":
        created_sections = list(created)

    db.commit()
    return {
        "kind": payload.kind,
        "created": len(created),
        "created_sections": len(created_sections),
        "reactivated": reactivated,
        "skipped": len(skipped),
        "locations": created,
        "sections": created_sections,
        "skipped_codes": skipped,
    }


def _natural_code(value: str) -> list[tuple[int, object]]:
    return [(1, int(part)) if part.isdigit() else (0, part.casefold()) for part in re.split(r"(\d+)", value or "")]


def _location_label_fonts() -> tuple[str, str]:
    global _LOCATION_LABEL_FONTS_READY
    regular_name, bold_name = "Helvetica", "Helvetica-Bold"
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if regular.exists() and bold.exists():
        if not _LOCATION_LABEL_FONTS_READY:
            pdfmetrics.registerFont(TTFont("FCLocation", str(regular)))
            pdfmetrics.registerFont(TTFont("FCLocationBold", str(bold)))
            _LOCATION_LABEL_FONTS_READY = True
        regular_name, bold_name = "FCLocation", "FCLocationBold"
    return regular_name, bold_name


def _fit_label_text(value: str, font: str, max_width: float, start_size: float, min_size: float) -> float:
    size = start_size
    while size > min_size and pdfmetrics.stringWidth(value, font, size) > max_width:
        size -= 0.5
    return size


def _draw_location_label(c: canvas.Canvas, location: Location, page_w: float, page_h: float) -> None:
    """Draw one warehouse QR label on an existing ReportLab page."""
    value = str(location.barcode or location.code or "").strip()
    title = str(location.code or "Адрес").strip()
    if not value:
        raise HTTPException(status_code=409, detail=f"У адреса {title} нет кода для QR")

    _, bold_font = _location_label_fonts()
    title_size = _fit_label_text(title, bold_font, page_w - 6 * mm, start_size=21, min_size=10)
    c.setFont(bold_font, title_size)
    title_y = page_h - max(6.8 * mm, min(8.5 * mm, page_h * 0.19))
    c.drawCentredString(page_w / 2, title_y, title)

    qr_bottom = 2 * mm
    qr_top = title_y - 2.4 * mm
    qr_size = min(page_w - 8 * mm, qr_top - qr_bottom)
    if qr_size < 14 * mm:
        raise HTTPException(status_code=400, detail="Размер этикетки слишком мал для QR ячейки")
    qr = createBarcodeDrawing(
        "QR",
        value=value,
        width=qr_size,
        height=qr_size,
        barLevel="M",
    )
    qr.drawOn(c, (page_w - qr_size) / 2, qr_bottom)


def _location_label_pdf(location: Location, width_mm: float, height_mm: float) -> bytes:
    """Warehouse label with a readable address at the top and a scannable QR below."""
    out = BytesIO()
    page_w, page_h = width_mm * mm, height_mm * mm
    c = canvas.Canvas(out, pagesize=(page_w, page_h))
    _draw_location_label(c, location, page_w, page_h)
    c.showPage()
    c.save()
    return out.getvalue()


# FC_LOCATION_AGENT_NATIVE_58X40_09415H_20260903
_LOCATION_LABEL_DOTS_PER_MM = 8


def _location_label_png(location: Location, width_mm: float, height_mm: float) -> bytes:
    """Render the browser-style location label in final 203-DPI printer dots.

    Print Agent previously received the same 58x40 PDF as the browser. Sumatra/Windows
    could rotate that landscape page according to the Xprinter driver profile, making
    the address run vertically along the label edge. A native 464x320 PNG bypasses PDF
    page orientation entirely while preserving the same title-above-QR geometry.
    """
    value = str(location.barcode or location.code or "").strip()
    title = str(location.code or "Адрес").strip()
    if not value:
        raise HTTPException(status_code=409, detail=f"У адреса {title} нет кода для QR")

    dots = _LOCATION_LABEL_DOTS_PER_MM
    width_px = max(1, int(round(float(width_mm) * dots)))
    height_px = max(1, int(round(float(height_mm) * dots)))
    image = Image.new("L", (width_px, height_px), 255)
    draw = ImageDraw.Draw(image)

    # Match the manual PDF: title centered at the top, QR centered below it.
    font_path = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    max_title_width = max(1, width_px - int(round(6 * dots)))
    start_font = max(16, int(round(21 * 203 / 72)))
    min_font = max(12, int(round(10 * 203 / 72)))
    font = None
    for size in range(start_font, min_font - 1, -1):
        try:
            candidate = ImageFont.truetype(str(font_path), size) if font_path.exists() else ImageFont.load_default()
        except Exception:
            candidate = ImageFont.load_default()
        bbox = draw.textbbox((0, 0), title, font=candidate)
        if bbox[2] - bbox[0] <= max_title_width:
            font = candidate
            break
    if font is None:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), title, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    title_baseline_from_top = int(round(max(6.8, min(8.5, float(height_mm) * 0.19)) * dots))
    # PIL positions by top-left; align the visual text bottom close to the ReportLab baseline.
    title_top = max(1, title_baseline_from_top - text_h - max(0, bbox[1]))
    draw.text(((width_px - text_w) // 2, title_top), title, fill=0, font=font)

    qr_bottom = int(round(2 * dots))
    qr_top_limit = title_baseline_from_top + int(round(2.4 * dots))
    qr_limit = min(width_px - int(round(8 * dots)), height_px - qr_bottom - qr_top_limit)
    if qr_limit < int(round(14 * dots)):
        raise HTTPException(status_code=400, detail="Размер этикетки слишком мал для QR ячейки")

    widget = QrCodeWidget(value=value, barLevel="M")
    widget.qr.make()
    modules = widget.qr.modules or []
    module_count = int(widget.qr.getModuleCount() or 0)
    if not modules or module_count <= 0:
        raise HTTPException(status_code=500, detail="Не удалось сформировать QR ячейки")
    border_modules = 4
    module_px = max(1, int(qr_limit) // (module_count + border_modules * 2))
    qr_px = (module_count + border_modules * 2) * module_px
    qr_left = (width_px - qr_px) // 2
    qr_top = height_px - qr_bottom - qr_px
    for row_idx, row in enumerate(modules):
        for col_idx, dark in enumerate(row):
            if not dark:
                continue
            left = qr_left + (col_idx + border_modules) * module_px
            top = qr_top + (row_idx + border_modules) * module_px
            draw.rectangle((left, top, left + module_px - 1, top + module_px - 1), fill=0)

    # Hard black/white image keeps QR edges sharp for direct TSPL printing.
    image = image.point(lambda px: 0 if px < 190 else 255, mode="1")
    out = BytesIO()
    image.save(out, format="PNG", optimize=False)
    return out.getvalue()



def _storage_package_metrics(raw):
    raw = raw if isinstance(raw, dict) else {}
    dims = raw.get("dimensions") or raw.get("dimension") or raw.get("packageDimensions") or {}
    if not isinstance(dims, dict):
        dims = {}
    def number(*values):
        for value in values:
            if value in (None, ""):
                continue
            try:
                parsed = float(str(value).replace(",", "."))
                if parsed > 0:
                    return parsed
            except Exception:
                pass
        return None
    length = number(dims.get("length"), dims.get("lengthCm"), raw.get("length"), raw.get("lengthCm"))
    width = number(dims.get("width"), dims.get("widthCm"), raw.get("width"), raw.get("widthCm"))
    height = number(dims.get("height"), dims.get("heightCm"), raw.get("height"), raw.get("heightCm"))
    weight = number(dims.get("weightBrutto"), dims.get("weight"), dims.get("weightKg"), raw.get("weightBrutto"), raw.get("weight"), raw.get("weightKg"), raw.get("packageWeight"))
    return {"volume_cm3": (length * width * height) if length and width and height else 0.0, "weight_kg": weight or 0.0}


@router.get("/location-occupancy")
def location_occupancy(warehouse_id: uuid.UUID, client_id: uuid.UUID | None = Query(default=None), db: Session = Depends(get_db)):
    """Show the client currently occupying each physical cell or pallet."""
    locations = db.execute(select(Location).where(
        Location.warehouse_id == warehouse_id,
        Location.is_active.is_(True),
    )).scalars().all()
    physical = {
        row.id: row
        for row in locations
        if str(row.code or "").strip().upper() not in SYSTEM_LOCATION_CODES
        and str(row.location_type or "").strip().upper() != "SECTION"
    }
    if not physical:
        return {"locations": []}

    data: dict[uuid.UUID, dict] = {
        location_id: {
            "location_id": str(location_id),
            "code": row.code,
            "owner_client_ids": set(),
            "quantity": 0.0,
            "sku_count": 0,
            "container_count": 0,
            "volume_cm3": 0.0,
            "weight_kg": 0.0,
            "volume_known_quantity": 0.0,
            "volume_unknown_quantity": 0.0,
        }
        for location_id, row in physical.items()
    }

    balance_rows = db.execute(
        select(
            InventoryBalance.location_id,
            InventoryBalance.client_id,
            func.coalesce(func.sum(InventoryBalance.quantity), 0),
            func.count(func.distinct(InventoryBalance.sku_id)),
        ).where(
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.location_id.in_(list(physical)),
            InventoryBalance.quantity > 0,
            *( [InventoryBalance.client_id == client_id] if client_id else [] ),
        ).group_by(InventoryBalance.location_id, InventoryBalance.client_id)
    ).all()
    for location_id, client_id, quantity, sku_count in balance_rows:
        item = data.get(location_id)
        if not item:
            continue
        if client_id:
            item["owner_client_ids"].add(str(client_id))
        item["quantity"] += float(quantity or 0)
        item["sku_count"] += int(sku_count or 0)

    # Empty boxes are not warehouse stock. Count a box only while it actually
    # contains positive physical inventory. A newly accepted empty carton can still
    # exist in the receiving workflow, but it must not make a storage address look
    # occupied in the stock/location screens.
    container_rows = db.execute(
        select(
            Container.location_id,
            Container.client_id,
            func.count(func.distinct(Container.id)),
        ).join(InventoryBalance, InventoryBalance.container_id == Container.id).where(
            Container.warehouse_id == warehouse_id,
            Container.location_id.in_(list(physical)),
            Container.status == "ACTIVE",
            InventoryBalance.warehouse_id == warehouse_id,
            InventoryBalance.client_id == Container.client_id,
            InventoryBalance.quantity > 0,
            *( [Container.client_id == client_id] if client_id else [] ),
        ).group_by(Container.location_id, Container.client_id)
    ).all()
    for location_id, client_id, container_count in container_rows:
        item = data.get(location_id)
        if not item:
            continue
        if client_id:
            item["owner_client_ids"].add(str(client_id))
        item["container_count"] += int(container_count or 0)

    # Estimate used standard-WB-carton volume from current physical balances.
    # One location is treated as a 60x40x40 storage carton when it is a normal cell;
    # the same percentage is still useful on pallet addresses as an equivalent carton load.
    if client_id:
        products = db.execute(select(MarketplaceProduct).where(
            MarketplaceProduct.client_id == client_id, MarketplaceProduct.provider == "WB"
        ).order_by(MarketplaceProduct.updated_at.desc())).scalars().all()
        barcode_metrics = {}
        for product in products:
            metrics = _storage_package_metrics(product.raw_data)
            codes = set(str(x).strip() for x in (product.barcodes or []) if x)
            for size in product.sizes or []:
                if isinstance(size, dict):
                    codes.update(str(x).strip() for x in (size.get("skus") or []) if x)
            for code in codes:
                barcode_metrics.setdefault(code, metrics)
        volume_rows = db.execute(
            select(InventoryBalance.location_id, InventoryBalance.quantity, SKU.barcode)
            .join(SKU, SKU.id == InventoryBalance.sku_id)
            .where(
                InventoryBalance.client_id == client_id,
                InventoryBalance.warehouse_id == warehouse_id,
                InventoryBalance.location_id.in_(list(physical)),
                InventoryBalance.quantity > 0,
            )
        ).all()
        for location_id, quantity, barcode in volume_rows:
            item = data.get(location_id)
            if not item:
                continue
            qty = float(quantity or 0)
            metrics = barcode_metrics.get(str(barcode or '').strip())
            if metrics and metrics.get("volume_cm3"):
                item["volume_cm3"] += float(metrics["volume_cm3"]) * qty
                item["weight_kg"] += float(metrics.get("weight_kg") or 0) * qty
                item["volume_known_quantity"] += qty
            else:
                item["volume_unknown_quantity"] += qty

    result = []
    for item in data.values():
        if not item["owner_client_ids"] and item["quantity"] <= 0 and item["container_count"] <= 0:
            continue
        item["owner_client_ids"] = sorted(item["owner_client_ids"])
        result.append(item)
    result.sort(key=lambda row: _natural_code(str(row.get("code") or "")))
    return {"locations": result}


def _location_label_rows(
    db: Session,
    *,
    warehouse_id: uuid.UUID,
    location_ids: list[uuid.UUID] | None,
    code_from: str | None,
    code_to: str | None,
    all_locations: bool,
) -> list[Location]:
    rows = db.execute(select(Location).where(
        Location.warehouse_id == warehouse_id,
        Location.is_active.is_(True),
    )).scalars().all()
    rows = [x for x in rows if str(x.code or "").strip().upper() not in SYSTEM_LOCATION_CODES]
    rows.sort(key=lambda x: _natural_code(str(x.code or "")))

    selected_ids = set(location_ids or [])
    if selected_ids:
        rows = [x for x in rows if x.id in selected_ids]
    elif code_from or code_to:
        codes = [str(x.code or "") for x in rows]
        start = 0
        end = len(rows) - 1
        if code_from:
            try:
                start = codes.index(code_from.strip())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=f"Начальная ячейка «{code_from}» не найдена") from exc
        if code_to:
            try:
                end = codes.index(code_to.strip())
            except ValueError as exc:
                raise HTTPException(status_code=404, detail=f"Конечная ячейка «{code_to}» не найдена") from exc
        if end < start:
            start, end = end, start
        rows = rows[start:end + 1]
    elif not all_locations:
        raise HTTPException(status_code=400, detail="Выберите ячейки, диапазон или печать всех адресов")

    if not rows:
        raise HTTPException(status_code=404, detail="Нет адресов для печати")
    if len(rows) > 2000:
        raise HTTPException(status_code=400, detail="За один раз можно распечатать не более 2000 этикеток")
    return rows


def _location_labels_document(rows: list[Location], width_mm: float, height_mm: float) -> bytes:
    """Build one multi-page QR PDF using ReportLab only.

    The previous batch path rendered hundreds of one-page PDFs and then merged them
    with pypdf/PyPDF2. Those merger libraries are not guaranteed to exist in every
    deployed FulfillCore image, so a multi-cell Print Agent request could fail with
    a generic HTTP 500 before any job ever reached the agent. Drawing every page on
    one ReportLab canvas removes that optional dependency and is substantially faster.
    """
    if not rows:
        raise HTTPException(status_code=404, detail="Нет адресов для печати")
    output = BytesIO()
    page_w, page_h = width_mm * mm, height_mm * mm
    c = canvas.Canvas(output, pagesize=(page_w, page_h))
    for location in rows:
        _draw_location_label(c, location, page_w, page_h)
        c.showPage()
    c.save()
    return output.getvalue()


@router.get("/locations/labels.pdf")
def location_labels_pdf(
    warehouse_id: uuid.UUID,
    location_id: list[uuid.UUID] | None = Query(default=None),
    code_from: str | None = Query(default=None),
    code_to: str | None = Query(default=None),
    all_locations: bool = Query(default=False),
    width_mm: float = Query(default=58, ge=25, le=120),
    height_mm: float = Query(default=40, ge=25, le=120),
    db: Session = Depends(get_db),
):
    """Build downloadable QR labels for one address, a range, or the whole warehouse."""
    rows = _location_label_rows(
        db,
        warehouse_id=warehouse_id,
        location_ids=location_id,
        code_from=code_from,
        code_to=code_to,
        all_locations=all_locations,
    )
    content = _location_labels_document(rows, width_mm, height_mm)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={"Content-Disposition": 'inline; filename="FulfillCore-location-labels.pdf"'},
    )


@router.post("/locations/labels.pdf")
def location_labels_pdf_post(payload: LocationLabelPdfRequest, db: Session = Depends(get_db)):
    """Build the same manual-print PDF without putting a long UUID list in the URL."""
    rows = _location_label_rows(
        db,
        warehouse_id=payload.warehouse_id,
        location_ids=payload.location_ids,
        code_from=payload.code_from,
        code_to=payload.code_to,
        all_locations=payload.all_locations,
    )
    content = _location_labels_document(rows, payload.width_mm, payload.height_mm)
    return Response(
        content=content,
        media_type="application/pdf",
        headers={
            "Content-Disposition": 'inline; filename="FulfillCore-location-labels.pdf"',
            "X-FulfillCore-Label-Count": str(len(rows)),
        },
    )


@router.post("/locations/labels/queue")
def queue_location_labels(payload: LocationLabelQueueRequest, db: Session = Depends(get_db)):
    """Queue native 58x40 location PNGs for Print Agent.

    Manual/browser printing keeps the existing PDF route. Print Agent receives one
    final-size 203-DPI PNG per cell so Windows/Sumatra cannot rotate the page.
    """
    rows = _location_label_rows(
        db,
        warehouse_id=payload.warehouse_id,
        location_ids=payload.location_ids,
        code_from=payload.code_from,
        code_to=payload.code_to,
        all_locations=payload.all_locations,
    )
    jobs = []
    for row in rows:
        document = _location_label_png(row, payload.width_mm, payload.height_mm)
        code = re.sub(r"[^0-9A-Za-zА-Яа-яЁё._-]+", "-", str(row.code or "location")).strip("-_") or "location"
        job = queue_print_job(
            db,
            station_code=payload.print_station,
            payload=document,
            file_name=f"FulfillCore-location-{code}.png",
            document_type="LOCATION_QR_LABEL",
            client_id=payload.client_id,
            object_type="LOCATION",
            object_id=str(row.id),
            content_type="image/png",
        )
        jobs.append(job)
    db.commit()
    job_ids = [str(job.id) for job in jobs]
    return {
        "ok": True,
        "print_job_id": job_ids[0] if job_ids else None,
        "print_job_ids": job_ids,
        "print_station": payload.print_station.strip().upper(),
        "labels_queued": len(rows),
        "batch_job": False,
        "native_raster": True,
        "label_dots": [int(round(payload.width_mm * _LOCATION_LABEL_DOTS_PER_MM)), int(round(payload.height_mm * _LOCATION_LABEL_DOTS_PER_MM))],
    }


@router.get("/locations/{location_id}/contents")
def location_contents(
    location_id: uuid.UUID,
    client_id: uuid.UUID,
    db: Session = Depends(get_db),
):
    """Return authoritative positive balances with WB-card-corrected display fields."""
    location = db.get(Location, location_id)
    if not location or not location.is_active:
        raise HTTPException(status_code=404, detail="Ячейка или палета не найдена")
    result = db.execute(
        select(InventoryBalance, SKU, Container)
        .join(SKU, SKU.id == InventoryBalance.sku_id)
        .outerjoin(Container, Container.id == InventoryBalance.container_id)
        .where(
            InventoryBalance.client_id == client_id,
            InventoryBalance.warehouse_id == location.warehouse_id,
            InventoryBalance.location_id == location.id,
            InventoryBalance.quantity > 0,
        )
        .order_by(Container.name, Container.barcode, SKU.article, SKU.size, SKU.barcode)
    ).all()
    identity_cache: dict[uuid.UUID, dict] = {}
    sku_ids = [sku.id for _, sku, _ in result]
    address_state: dict[tuple[str,str,str], dict[str, Decimal]] = {}
    if sku_ids:
        ids=[str(x) for x in set(sku_ids)]
        stmt=text("""
            SELECT r.sku_id,r.source_location_id,COALESCE(r.source_container_id::text,'') AS container_key,
              COALESCE(SUM(CASE WHEN o.local_status IN ('NEW','PICKING') THEN r.quantity ELSE 0 END),0) AS reserved,
              COALESCE(SUM(CASE WHEN COALESCE(o.local_status,'') NOT IN ('NEW','PICKING') AND NOT (r.status='PACKING_WRITE_OFF' AND io.id IS NOT NULL) THEN r.quantity ELSE 0 END),0) AS accounting_pending
            FROM fc_stock_reservations r
            LEFT JOIN marketplace_orders o ON CAST(o.id AS varchar)=r.order_id
            LEFT JOIN inventory_operations io
              ON CAST(io.client_id AS varchar)=CAST(r.client_id AS varchar) AND COALESCE(io.status,'COMPLETED')='COMPLETED'
             AND io.idempotency_key=('stock-core-v2-pack-' || CAST(o.id AS varchar) || '-' || CAST(r.id AS varchar))
            WHERE r.client_id=:client AND r.warehouse_id=:warehouse AND r.sku_id IN :ids AND r.source_location_id IS NOT NULL
              AND r.status IN ('ACTIVE','CONSUMING','CANCELLING','PACKING_WRITE_OFF')
            GROUP BY r.sku_id,r.source_location_id,COALESCE(r.source_container_id::text,'')
        """).bindparams(bindparam("ids",expanding=True))
        try:
            for x in db.execute(stmt,{"client":str(client_id),"warehouse":str(location.warehouse_id),"ids":ids}).mappings(): address_state[(str(x["sku_id"]),str(x["source_location_id"]),str(x["container_key"] or ""))]={"reserved":Decimal(str(x["reserved"] or 0)),"pending":Decimal(str(x["accounting_pending"] or 0))}
        except Exception: address_state={}
    # FC_K9_LOCATION_BATCH_TRUTH: one batch instead of several SQL statements per SKU.
    # This keeps the location card on the exact same reserve/free equation as Inventory,
    # without the N+1 query pattern that caused Request timeout on multi-SKU cells.
    try:
        warehouse_truth=stock_truth_for_skus(
            db,client_id=client_id,warehouse_id=location.warehouse_id,sku_ids=set(sku_ids)
        )
    except Exception:
        warehouse_truth={}
    # FC_K9R4_LOCATION_IDENTITY_BATCH: resolving WB product metadata once per SKU used
    # to issue another DB read for every line in a busy cell. Resolve all exact barcodes
    # in one query and fall back to the local SKU fields without blocking the cell.
    barcode_set = {str(sku.barcode or '').strip() for _, sku, _ in result if str(sku.barcode or '').strip()}
    try:
        identity_by_barcode = wb_product_identity_map(db, client_id, barcode_set) if barcode_set else {}
    except Exception:
        identity_by_barcode = {}
    rows = []
    for balance, sku, container in result:
        identity = identity_cache.get(sku.id)
        if identity is None:
            code = str(sku.barcode or '').strip()
            identity = identity_by_barcode.get(code) or {
                'article': sku.article or sku.sku_code, 'sku_code': sku.sku_code,
                'name': sku.name or sku.article or sku.sku_code, 'size': sku.size,
                'barcode': code or None, 'raw_barcode': code or None,
                'barcode_was_duplicated': False,
            }
            identity_cache[sku.id] = identity
        bucket=address_state.get((str(sku.id),str(location.id),str(container.id) if container else ""),{})
        address_reserved=bucket.get("reserved",Decimal(0)); address_pending=bucket.get("pending",Decimal(0))
        total_state=warehouse_truth.get(str(sku.id),{}); warehouse_physical=Decimal(str(total_state.get("physical",balance.quantity))); sale_reserved=Decimal(str(total_state.get("sale_reserved",0))); picking_reserved=Decimal(str(total_state.get("picking_reserved",0))); accounting_pending=Decimal(str(total_state.get("accounting_pending",0))); warehouse_reserved=sale_reserved+picking_reserved; warehouse_free=Decimal(str(total_state.get("available_to_sell",max(Decimal(0),warehouse_physical-warehouse_reserved-accounting_pending))))
        warehouse_received=Decimal(str(total_state.get("received",0))); warehouse_shipped=Decimal(str(total_state.get("shipped",0))); warehouse_adjustment_in=Decimal(str(total_state.get("adjustment_in",0))); warehouse_adjustment_out=Decimal(str(total_state.get("adjustment_out",0))); warehouse_opening_gap=Decimal(str(total_state.get("opening_balance_estimate",0)))
        rows.append({
            "balance_id": str(balance.id),
            "sku_id": str(sku.id),
            "location_id": str(location.id),
            "container_id": str(container.id) if container else None,
            "container": (container.name or container.barcode) if container else None,
            "quantity": str(balance.quantity),
            "reserved_quantity": str(address_reserved),
            "accounting_pending_quantity": str(address_pending),
            "free_quantity": str(max(Decimal(0), Decimal(balance.quantity) - address_reserved - address_pending)),
            "warehouse_quantity": str(warehouse_physical),
            "sale_reserved_quantity": str(sale_reserved),
            "picking_reserved_quantity": str(picking_reserved),
            "warehouse_reserved_quantity": str(warehouse_reserved),
            "warehouse_accounting_pending_quantity": str(accounting_pending),
            "warehouse_free_quantity": str(warehouse_free),
            "warehouse_received_quantity": str(warehouse_received),
            "warehouse_shipped_quantity": str(warehouse_shipped),
            "warehouse_adjustment_in_quantity": str(warehouse_adjustment_in),
            "warehouse_adjustment_out_quantity": str(warehouse_adjustment_out),
            "warehouse_opening_balance_estimate": str(warehouse_opening_gap),
            **identity,
        })
    return {
        "location": {
            "id": str(location.id),
            "warehouse_id": str(location.warehouse_id),
            "code": location.code,
            "name": location.name,
            "location_type": location.location_type,
        },
        "items": rows,
    }


@router.post("/locations/{location_id}/stock/remove")
def remove_location_stock(
    location_id: uuid.UUID,
    payload: LocationStockRemoveRequest,
    db: Session = Depends(get_db),
):
    """Remove one erroneous cell/box balance through a traceable adjustment."""
    location = db.get(Location, location_id)
    if not location or not location.is_active or location.warehouse_id != payload.warehouse_id:
        raise HTTPException(status_code=404, detail="Ячейка или палета не найдена на выбранном складе")
    balance = db.execute(
        select(InventoryBalance)
        .where(
            InventoryBalance.client_id == payload.client_id,
            InventoryBalance.warehouse_id == payload.warehouse_id,
            InventoryBalance.location_id == location.id,
            InventoryBalance.sku_id == payload.sku_id,
            InventoryBalance.container_id == payload.container_id,
            InventoryBalance.quantity > 0,
        )
        .with_for_update()
    ).scalars().first()
    if not balance:
        raise HTTPException(status_code=404, detail="Остаток в этой ячейке уже отсутствует")
    quantity = Decimal(payload.quantity)
    if quantity > Decimal(balance.quantity):
        raise HTTPException(
            status_code=409,
            detail=f"В выбранной строке доступно только {balance.quantity} шт.",
        )
    operation = execute_operation(db, OperationCreate(
        client_id=payload.client_id,
        warehouse_id=payload.warehouse_id,
        operation_type="ADJUSTMENT",
        idempotency_key=str(uuid.uuid4()),
        performed_by=payload.performed_by,
        comment="Удаление ошибочно принятого товара из ячейки",
        lines=[StockLine(
            sku_id=payload.sku_id,
            quantity=quantity,
            source_location_id=location.id,
            source_container_id=payload.container_id,
            reason="REMOVE_ERRONEOUS_LOCATION_STOCK",
        )],
    ))
    if payload.container_id:
        cleanup=archive_consumed_empty_containers(
            db,payload.client_id,payload.warehouse_id,[payload.container_id],
            actor=payload.performed_by,reason="LOCATION_STOCK_REMOVE_EMPTY_BOX",
        )
        if cleanup.get("count"):
            db.commit()
    return {
        "ok": True,
        "operation_id": str(operation.id),
        "quantity_removed": str(quantity),
        "remaining": str(Decimal(balance.quantity)),
    }


@router.get("/locations/{location_id}/history")
def location_history(
    location_id: uuid.UUID,
    client_id: uuid.UUID,
    limit: int = Query(default=1000, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    """Return the complete movement history that touched one physical address."""
    location = db.get(Location, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Ячейка или палета не найдена")

    result_rows = db.execute(
        select(InventoryOperationLine, InventoryOperation)
        .join(InventoryOperation, InventoryOperation.id == InventoryOperationLine.operation_id)
        .where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == location.warehouse_id,
            or_(
                InventoryOperationLine.source_location_id == location.id,
                InventoryOperationLine.destination_location_id == location.id,
            ),
        )
        .order_by(InventoryOperation.created_at.desc(), InventoryOperationLine.id.desc())
        .limit(limit + 1)
    ).all()

    has_more = len(result_rows) > limit
    result_rows = result_rows[:limit]

    lines = [line for line, _ in result_rows]
    location_ids = {
        value
        for line in lines
        for value in (line.source_location_id, line.destination_location_id)
        if value is not None
    }
    container_ids = {
        value
        for line in lines
        for value in (line.source_container_id, line.destination_container_id)
        if value is not None
    }
    sku_ids = {line.sku_id for line in lines}

    locations = db.execute(select(Location).where(Location.id.in_(location_ids))).scalars().all() if location_ids else []
    containers = db.execute(select(Container).where(Container.id.in_(container_ids))).scalars().all() if container_ids else []
    skus = db.execute(select(SKU).where(SKU.id.in_(sku_ids))).scalars().all() if sku_ids else []
    location_by_id = {row.id: row for row in locations}
    container_by_id = {row.id: row for row in containers}
    sku_by_id = {row.id: row for row in skus}

    def location_code(value: uuid.UUID | None) -> str | None:
        row = location_by_id.get(value)
        return str(row.code) if row else None

    def container_name(value: uuid.UUID | None) -> str | None:
        row = container_by_id.get(value)
        return str(row.name or row.barcode) if row else None

    # FC_K9R62_LOCATION_ZERO_REFILL_HISTORY
    # FC_K9R63_PHYSICAL_PLACE_HISTORY: a box can be refilled with a different SKU.
    # Full-ledger classification happens before the visible movement-row limit.
    from app.services.place_history import place_history
    place_events = place_history(
        db, client_id=client_id, warehouse_id=location.warehouse_id, location_id=location.id,
    )
    first_line_by_operation = {}
    for line, operation in result_rows:
        first_line_by_operation.setdefault(str(operation.id), line.id)
    place_events_by_line = {}
    for event in place_events:
        line_id = first_line_by_operation.get(event['operation_id'])
        if line_id is not None:
            place_events_by_line.setdefault(line_id, []).append(event)

    # A cancelled receipt with an explicit reversal did affect physical stock at
    # its original time. Keep both events rather than erasing the original receipt.
    cancelled_ids = {str(operation.id) for _, operation in result_rows
                     if str(operation.status or '').upper() == 'CANCELLED'}
    reversed_ids = set()
    if cancelled_ids:
        reversal_keys = db.execute(select(InventoryOperation.idempotency_key).where(
            InventoryOperation.client_id == client_id,
            InventoryOperation.warehouse_id == location.warehouse_id,
            InventoryOperation.idempotency_key.in_(['cancel-receiving-' + key for key in cancelled_ids]),
            func.upper(func.coalesce(InventoryOperation.status, 'COMPLETED')) != 'CANCELLED',
        )).scalars().all()
        reversed_ids = {key.removeprefix('cancel-receiving-') for key in reversal_keys}

    rows = []
    history_identity_cache: dict[uuid.UUID, dict] = {}
    history_barcodes = {str(sku.barcode or '').strip() for sku in skus if str(sku.barcode or '').strip()}
    try:
        history_identity_by_barcode = wb_product_identity_map(db, client_id, history_barcodes) if history_barcodes else {}
    except Exception:
        history_identity_by_barcode = {}
    for line, operation in result_rows:
        sku = sku_by_id.get(line.sku_id)
        identity = None
        if sku:
            identity = history_identity_cache.get(sku.id)
            if identity is None:
                code = str(sku.barcode or '').strip()
                identity = history_identity_by_barcode.get(code) or {
                    'article': sku.article or sku.sku_code, 'sku_code': sku.sku_code,
                    'name': sku.name or sku.article or sku.sku_code, 'size': sku.size,
                    'barcode': code or None, 'raw_barcode': code or None,
                    'barcode_was_duplicated': False,
                }
                history_identity_cache[sku.id] = identity
        rows.append({
            "id": str(line.id),
            "operation_id": str(operation.id),
            "created_at": operation.created_at,
            "operation_type": operation.operation_type,
            "status": operation.status,
            "was_reversed": str(operation.id) in reversed_ids,
            "performed_by": operation.performed_by,
            "reference": operation.reference,
            "comment": operation.comment,
            "reason": line.reason,
            "sku_id": str(line.sku_id),
            "sku": {
                "article": identity.get("article"),
                "sku_code": identity.get("sku_code"),
                "name": identity.get("name"),
                "size": identity.get("size"),
                "barcode": identity.get("barcode"),
                "raw_barcode": identity.get("raw_barcode"),
                "barcode_was_duplicated": identity.get("barcode_was_duplicated"),
            } if identity else None,
            "quantity": str(line.quantity),
            "source_location_id": str(line.source_location_id) if line.source_location_id else None,
            "source_location": location_code(line.source_location_id),
            "source_container_id": str(line.source_container_id) if line.source_container_id else None,
            "source_container": container_name(line.source_container_id),
            "destination_location_id": str(line.destination_location_id) if line.destination_location_id else None,
            "destination_location": location_code(line.destination_location_id),
            "destination_container_id": str(line.destination_container_id) if line.destination_container_id else None,
            "destination_container": container_name(line.destination_container_id),
            "place_events": place_events_by_line.get(line.id, []),
        })

    return {
        "location": {
            "id": str(location.id),
            "code": location.code,
            "name": location.name,
            "location_type": location.location_type,
        },
        "rows": rows,
        "place_history": place_events,
        "limit": limit,
        "has_more": has_more,
    }


@router.delete("/containers/{container_id}")
def delete_empty_container(container_id: uuid.UUID, db: Session = Depends(get_db)):
    """Archive only an empty box; operation history remains addressable by its ID."""
    container = db.get(Container, container_id)
    if not container:
        raise HTTPException(status_code=404, detail="Короб не найден")
    if str(container.status or "").upper() != "ACTIVE":
        return {"deleted": True, "archived": True, "name": container.name or container.barcode}
    balance = db.execute(select(InventoryBalance).where(
        InventoryBalance.container_id == container.id,
        InventoryBalance.quantity != 0,
    ).with_for_update()).scalars().first()
    if balance:
        raise HTTPException(status_code=409, detail="Короб содержит товар. Сначала переместите или спишите остатки.")
    label = container.name or container.barcode
    container.status = "ARCHIVED"
    db.commit()
    return {"deleted": True, "archived": True, "name": label}


def _cleanup_consumed_empty_boxes_at_location(db: Session, location: Location, reason: str) -> int:
    rows = db.execute(select(Container.id, Container.client_id).where(
        Container.location_id == location.id,
        Container.status == "ACTIVE",
    )).all()
    by_client: dict[uuid.UUID, list[uuid.UUID]] = {}
    for container_id, client_id in rows:
        if client_id:
            by_client.setdefault(client_id, []).append(container_id)
    total = 0
    for client_id, ids in by_client.items():
        total += int(archive_consumed_empty_containers(
            db, client_id, location.warehouse_id, ids, reason=reason,
        ).get("count", 0))
    return total


@router.delete("/locations/{location_id}")
def delete_empty_location(location_id: uuid.UUID, db: Session = Depends(get_db)):
    location = db.get(Location, location_id)
    if not location:
        raise HTTPException(status_code=404, detail="Ячейка или палета не найдена")
    if str(location.code or "").strip().upper() in SYSTEM_LOCATION_CODES:
        raise HTTPException(status_code=409, detail="Системный адрес удалить нельзя")
    if not location.is_active:
        return {"deleted": True, "archived": True, "code": location.code}

    balance = db.execute(select(InventoryBalance).where(
        InventoryBalance.location_id == location.id,
        InventoryBalance.quantity != 0,
    )).scalars().first()
    if balance:
        raise HTTPException(status_code=409, detail="Нельзя удалить адрес с товаром. Сначала переместите остатки.")

    # Consumed boxes must never block reuse/deletion of an otherwise empty cell or
    # pallet place. Newly accepted empty boxes are protected by the lifecycle helper
    # until they have actually contained stock.
    _cleanup_consumed_empty_boxes_at_location(
        db, location, "LOCATION_DELETE_EMPTY_BOX_CLEANUP"
    )

    container = db.execute(select(Container).where(
        Container.location_id == location.id,
        Container.status == "ACTIVE",
    )).scalars().first()
    if container:
        raise HTTPException(status_code=409, detail="Нельзя удалить адрес, пока к нему привязан короб. Сначала переместите или удалите короб.")

    child = db.execute(select(Location).where(
        Location.parent_id == location.id,
        Location.is_active.is_(True),
    )).scalars().first()
    if child:
        raise HTTPException(status_code=409, detail="Нельзя удалить адрес, у которого есть вложенные ячейки.")

    label = location.code
    location.is_active = False
    db.commit()
    return {"deleted": True, "archived": True, "code": label}


def _archive_container_for_bulk(db: Session, container: Container) -> str | None:
    if str(container.status or "").upper() != "ACTIVE":
        return "уже удалён"
    balance = db.execute(select(InventoryBalance.id).where(
        InventoryBalance.container_id == container.id,
        InventoryBalance.quantity != 0,
    )).first()
    if balance:
        return "в коробе есть товар"
    container.status = "ARCHIVED"
    return None


def _archive_location_for_bulk(db: Session, location: Location) -> str | None:
    if not location.is_active:
        return "уже удалена"
    if str(location.code or "").strip().upper() in SYSTEM_LOCATION_CODES:
        return "системный адрес"
    if db.execute(select(InventoryBalance.id).where(
        InventoryBalance.location_id == location.id,
        InventoryBalance.quantity != 0,
    )).first():
        return "в ячейке есть товар"
    _cleanup_consumed_empty_boxes_at_location(
        db, location, "BULK_LOCATION_DELETE_EMPTY_BOX_CLEANUP"
    )
    if db.execute(select(Container.id).where(
        Container.location_id == location.id,
        Container.status == "ACTIVE",
    )).first():
        return "в ячейке есть активный короб"
    if db.execute(select(Location.id).where(
        Location.parent_id == location.id,
        Location.is_active.is_(True),
    )).first():
        return "есть вложенные активные адреса"
    location.is_active = False
    return None


@router.post("/bulk-delete/containers")
def bulk_delete_empty_containers(payload: BulkArchiveRequest, db: Session = Depends(get_db)):
    rows = db.execute(select(Container).where(Container.id.in_(payload.ids)).with_for_update()).scalars().all()
    by_id = {row.id: row for row in rows}
    deleted, skipped = [], []
    for item_id in payload.ids:
        row = by_id.get(item_id)
        if not row:
            skipped.append({"id": str(item_id), "reason": "короб не найден"})
            continue
        reason = _archive_container_for_bulk(db, row)
        label = str(row.name or row.barcode)
        if reason:
            skipped.append({"id": str(item_id), "name": label, "reason": reason})
        else:
            deleted.append({"id": str(item_id), "name": label})
    db.commit()
    return {"deleted": deleted, "skipped": skipped}


@router.post("/bulk-delete/locations")
def bulk_delete_empty_locations(payload: BulkArchiveRequest, db: Session = Depends(get_db)):
    rows = db.execute(select(Location).where(Location.id.in_(payload.ids)).with_for_update()).scalars().all()
    by_id = {row.id: row for row in rows}
    deleted, skipped = [], []
    for item_id in payload.ids:
        row = by_id.get(item_id)
        if not row:
            skipped.append({"id": str(item_id), "reason": "адрес не найден"})
            continue
        reason = _archive_location_for_bulk(db, row)
        label = str(row.code or item_id)
        if reason:
            skipped.append({"id": str(item_id), "code": label, "reason": reason})
        else:
            deleted.append({"id": str(item_id), "code": label})
    db.commit()
    return {"deleted": deleted, "skipped": skipped}
