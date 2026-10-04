from io import BytesIO
from pathlib import Path
import re

from reportlab.graphics.barcode import code128
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

_FONT_READY = False


def _ensure_label_fonts() -> tuple[str, str]:
    global _FONT_READY
    regular_name, bold_name = "Helvetica", "Helvetica-Bold"
    regular = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    bold = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if regular.exists() and bold.exists():
        if not _FONT_READY:
            pdfmetrics.registerFont(TTFont("FCDejaVu", str(regular)))
            pdfmetrics.registerFont(TTFont("FCDejaVuBold", str(bold)))
            _FONT_READY = True
        regular_name, bold_name = "FCDejaVu", "FCDejaVuBold"
    return regular_name, bold_name


def _clean(value: str | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


def _fit_text(text: str, font: str, max_width: float, start_size: float = 10, min_size: float = 4.5) -> float:
    size = float(start_size)
    while size > min_size and pdfmetrics.stringWidth(text, font, size) > max_width:
        size -= 0.2
    return max(min_size, size)


def _wrap(text: str, font: str, size: float, max_width: float, max_lines: int = 2) -> list[str]:
    text = _clean(text)
    if not text:
        return []
    words = text.split()
    lines: list[str] = []
    cur = ""
    consumed = 0
    for word in words:
        trial = word if not cur else cur + " " + word
        if pdfmetrics.stringWidth(trial, font, size) <= max_width:
            cur = trial
            consumed += 1
            continue
        if cur:
            lines.append(cur)
        cur = word
        consumed += 1
        if len(lines) >= max_lines - 1:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    # Add ellipsis only if the source did not fit into the requested line count.
    if len(lines) == max_lines and consumed < len(words):
        while lines[-1] and pdfmetrics.stringWidth(lines[-1] + "...", font, size) > max_width:
            lines[-1] = lines[-1][:-1]
        lines[-1] = lines[-1].rstrip() + "..."
    return lines[:max_lines]


def _apply_page_transform(c: canvas.Canvas, page_w: float, page_h: float, rotate: int, scale_percent: float, offset_x_mm: float, offset_y_mm: float):
    """Transform logical label coordinates onto a physical page.

    rotate=90 means a logical 40x58 portrait design is placed on a physical 58x40 page.
    Offsets are always in physical printer coordinates: +X = right, +Y = up.
    """
    rotate = int(rotate or 0) % 360
    scale = _clamp(scale_percent, 80.0, 110.0) / 100.0
    ox = _clamp(offset_x_mm, -5.0, 5.0) * mm
    oy = _clamp(offset_y_mm, -5.0, 5.0) * mm

    c.saveState()
    c.translate(page_w / 2 + ox, page_h / 2 + oy)
    c.scale(scale, scale)
    if rotate:
        c.rotate(rotate)
    c.translate(-page_w / 2, -page_h / 2)
    return scale


def _draw_image_fit(c: canvas.Canvas, raw: bytes, page_w: float, page_h: float, rotate: int = -1,
                    scale_percent: float = 100.0, offset_x_mm: float = 0.0, offset_y_mm: float = 0.0,
                    safe_margin_mm: float = 0.6) -> None:
    """Draw a WB sticker centred inside the exact physical page.

    rotate=-1 auto-rotates the source if its portrait/landscape orientation differs from the media.
    The source is never distorted and never clipped; only a small scanner-safe margin is kept.
    """
    img = ImageReader(BytesIO(raw))
    iw, ih = img.getSize()
    page_landscape = page_w >= page_h
    image_landscape = iw >= ih
    actual_rotate = int(rotate)
    if actual_rotate < 0:
        actual_rotate = 90 if page_landscape != image_landscape else 0
    actual_rotate %= 360

    if actual_rotate in (90, 270):
        rw, rh = ih, iw
    else:
        rw, rh = iw, ih

    margin = _clamp(safe_margin_mm, 0.0, 4.0) * mm
    avail_w = max(1.0, page_w - 2 * margin)
    avail_h = max(1.0, page_h - 2 * margin)
    fit = min(avail_w / rw, avail_h / rh)
    fit *= _clamp(scale_percent, 85.0, 108.0) / 100.0
    draw_w, draw_h = rw * fit, rh * fit

    cx = page_w / 2 + _clamp(offset_x_mm, -5.0, 5.0) * mm
    cy = page_h / 2 + _clamp(offset_y_mm, -5.0, 5.0) * mm

    c.saveState()
    c.translate(cx, cy)
    c.rotate(actual_rotate)
    # After rotating the canvas, draw dimensions are based on the unrotated source.
    src_w = iw * fit
    src_h = ih * fit
    c.drawImage(img, -src_w / 2, -src_h / 2, width=src_w, height=src_h,
                preserveAspectRatio=True, anchor='c', mask='auto')
    c.restoreState()


def png_pages_to_pdf(images: list[bytes], width_mm: float = 58, height_mm: float = 40,
                     rotate: int = -1, fill: bool = False, *, scale_percent: float = 100.0,
                     offset_x_mm: float = 0.0, offset_y_mm: float = 0.0,
                     safe_margin_mm: float = 0.6) -> bytes:
    """Convert WB sticker images to exact-size printer pages.

    0.7.8.4 intentionally does not stretch the source image. The sticker is auto-rotated,
    centred and fitted to the physical media. This prevents the previous off-centre/clipped
    labels when a 40x58 source was printed on a 58x40 media profile.
    """
    if not images:
        raise ValueError("Нет изображений для печати")
    width_mm = _clamp(width_mm, 25.0, 120.0)
    height_mm = _clamp(height_mm, 25.0, 120.0)
    out = BytesIO()
    pw, ph = width_mm * mm, height_mm * mm
    c = canvas.Canvas(out, pagesize=(pw, ph), pageCompression=1)
    for raw in images:
        _draw_image_fit(c, raw, pw, ph, rotate=rotate, scale_percent=scale_percent,
                        offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,
                        safe_margin_mm=safe_margin_mm)
        c.showPage()
    c.save()
    return out.getvalue()


def _draw_product_label(c: canvas.Canvas, logical_w: float, logical_h: float, barcode: str,
                        article: str | None, size: str | None, *, title: str | None = None,
                        brand: str | None = None, composition: str | None = None,
                        color: str | None = None, country: str | None = None) -> None:
    """Draw one portrait retail layout in logical coordinates."""
    regular, bold = _ensure_label_fonts()
    margin = 1.35 * mm
    usable_w = logical_w - 2 * margin
    top = logical_h - 1.45 * mm

    # Bottom barcode zone is reserved first so text can never collide with it.
    number_y = 1.55 * mm
    bar_y = 5.0 * mm
    bar_h = 11.0 * mm
    text_floor = 18.0 * mm

    size_box_w = 12.2 * mm if size else 0
    gap = 1.0 * mm if size else 0
    text_w = usable_w - size_box_w - gap
    y = top

    if title:
        title_size = 5.9
        title_lines = _wrap(title, bold, title_size, text_w, 2)
        for row in title_lines:
            c.setFont(bold, title_size)
            c.drawString(margin, y - title_size, row)
            y -= 3.0 * mm

    if size:
        bx = logical_w - margin - size_box_w
        bh = 12.7 * mm
        by = logical_h - 1.8 * mm - bh
        c.setLineWidth(0.65)
        c.roundRect(bx, by, size_box_w, bh, 0.7 * mm)
        txt = _clean(size)
        fs = _fit_text(txt, bold, size_box_w - 1.3 * mm, 10.5, 6.0)
        c.setFont(bold, fs)
        c.drawCentredString(bx + size_box_w / 2, by + 6.2 * mm, txt)
        c.setFont(regular, 4.8)
        c.drawCentredString(bx + size_box_w / 2, by + 1.6 * mm, "Размер")

    def info(label: str, value: str | None, *, bold_value: bool = False, lines: int = 1):
        nonlocal y
        value = _clean(value)
        if not value or y <= text_floor:
            return
        txt = f"{label}: {value}" if label else value
        font = bold if bold_value else regular
        fs = 5.55
        for row in _wrap(txt, font, fs, text_w, lines):
            if y <= text_floor:
                return
            c.setFont(font, fs)
            c.drawString(margin, y - fs, row)
            y -= 2.35 * mm

    info("Арт.", article, bold_value=True)
    info("Бренд", brand)
    info("Цвет", color)
    info("Состав", composition, lines=2)
    info("Страна", country)

    # Barcode takes almost the full width but keeps quiet zones at both sides.
    max_bar_w = logical_w - 3.2 * mm
    bc = code128.Code128(barcode, barHeight=bar_h, barWidth=0.29 * mm, humanReadable=False)
    scale_x = min(1.0, max_bar_w / max(bc.width, 1.0))
    c.saveState()
    c.translate((logical_w - bc.width * scale_x) / 2, bar_y)
    c.scale(scale_x, 1.0)
    bc.drawOn(c, 0, 0)
    c.restoreState()

    fs = _fit_text(barcode, bold, max_bar_w, 7.6, 5.2)
    c.setFont(bold, fs)
    c.drawCentredString(logical_w / 2, number_y, barcode)


def product_barcode_label_pdf(barcode: str, article: str | None = None, size: str | None = None,
                              width_mm: float = 58, height_mm: float = 40, *, rotate: int = 90,
                              scale_percent: float = 100.0, offset_x_mm: float = 0.0,
                              offset_y_mm: float = 0.0, title: str | None = None,
                              brand: str | None = None, composition: str | None = None,
                              color: str | None = None, country: str | None = None) -> bytes:
    """Generate an exact-size product label for a 58x40 thermal printer.

    Default media is 58x40 mm. The product layout is portrait, therefore it is rotated 90°
    *inside the PDF page* instead of asking the printer driver to rotate or scale the page.
    This is the key fix for the clipped/off-centre labels seen with 40x58 PDF pages.
    """
    barcode = _clean(barcode)
    if not barcode:
        raise ValueError("Не указан штрихкод товара")
    width_mm = _clamp(width_mm, 25.0, 120.0)
    height_mm = _clamp(height_mm, 25.0, 120.0)
    rotate = int(rotate or 0) % 360

    pw, ph = width_mm * mm, height_mm * mm
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(pw, ph), pageCompression=1)

    # Transform about the centre in physical printer coordinates.
    c.saveState()
    c.translate(pw / 2 + _clamp(offset_x_mm, -5.0, 5.0) * mm,
                ph / 2 + _clamp(offset_y_mm, -5.0, 5.0) * mm)
    scale = _clamp(scale_percent, 80.0, 108.0) / 100.0
    c.scale(scale, scale)
    c.rotate(rotate)

    # Logical canvas dimensions after rotation. 58x40 @ 90° -> logical 40x58.
    if rotate in (90, 270):
        logical_w, logical_h = ph, pw
    else:
        logical_w, logical_h = pw, ph
    c.translate(-logical_w / 2, -logical_h / 2)
    _draw_product_label(c, logical_w, logical_h, barcode, article, size,
                        title=title, brand=brand, composition=composition,
                        color=color, country=country)
    c.restoreState()

    c.showPage()
    c.save()
    return out.getvalue()


def print_calibration_pdf(kind: str = "product", width_mm: float = 58, height_mm: float = 40,
                          rotate: int = 90, scale_percent: float = 100.0,
                          offset_x_mm: float = 0.0, offset_y_mm: float = 0.0) -> bytes:
    """A deterministic test label used by the Print Settings screen."""
    if kind == "product":
        return product_barcode_label_pdf(
            "2051090535895", "183321", "M-L 48-50", width_mm=width_mm, height_mm=height_mm,
            rotate=rotate, scale_percent=scale_percent, offset_x_mm=offset_x_mm,
            offset_y_mm=offset_y_mm, title="Костюм мужской графитовый", brand="TM LIMITED",
            composition="50% вискоза; 28% полиэстер; 22% нейлон", color="графитовый",
            country="Киргизия",
        )

    regular, bold = _ensure_label_fonts()
    width_mm = _clamp(width_mm, 25.0, 120.0)
    height_mm = _clamp(height_mm, 25.0, 120.0)
    pw, ph = width_mm * mm, height_mm * mm
    out = BytesIO()
    c = canvas.Canvas(out, pagesize=(pw, ph), pageCompression=1)
    c.setLineWidth(0.7)
    c.rect(0.8 * mm, 0.8 * mm, pw - 1.6 * mm, ph - 1.6 * mm)
    c.setFont(bold, 10)
    c.drawCentredString(pw / 2, ph - 6 * mm, f"WB TEST {width_mm:g}x{height_mm:g} mm")
    c.setFont(regular, 6)
    c.drawCentredString(pw / 2, ph - 10 * mm, "Рамка должна быть целиком внутри этикетки")
    bc = code128.Code128("57093452429", barHeight=11 * mm, barWidth=0.33 * mm, humanReadable=False)
    sx = min(1.0, (pw - 8 * mm) / max(bc.width, 1.0))
    c.saveState(); c.translate((pw - bc.width * sx) / 2, 7 * mm); c.scale(sx, 1); bc.drawOn(c, 0, 0); c.restoreState()
    c.setFont(bold, 8); c.drawCentredString(pw / 2, 3.2 * mm, "57093452429")
    c.showPage(); c.save()
    return out.getvalue()
