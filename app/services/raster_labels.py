from __future__ import annotations

from io import BytesIO
from pathlib import Path
import math
import re

from PIL import Image, ImageDraw, ImageFont, ImageOps
from reportlab.graphics.barcode import code128

# XP-420B is a 203-DPI / 8-dots-per-mm printer.  Building the final image in
# printer dots removes PDF page boxes, Windows-driver margins, rotation and
# "fit to page" from the label path.
DOTS_PER_MM = 8
DEFAULT_WIDTH_MM = 58
DEFAULT_HEIGHT_MM = 40

_FONT_REGULAR = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"
_FONT_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


def _clean(value: object | None) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _px(mm_value: float) -> int:
    return max(1, int(round(float(mm_value) * DOTS_PER_MM)))


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    path = Path(_FONT_BOLD if bold else _FONT_REGULAR)
    if path.exists():
        return ImageFont.truetype(str(path), max(8, int(size)))
    return ImageFont.load_default()


def _text_width(draw: ImageDraw.ImageDraw, text: str, font) -> int:
    box = draw.textbbox((0, 0), text, font=font)
    return max(0, box[2] - box[0])


def _fit_font(draw: ImageDraw.ImageDraw, text: str, max_width: int, start: int, minimum: int, *, bold: bool = False):
    for size in range(int(start), int(minimum) - 1, -1):
        f = _font(size, bold)
        if _text_width(draw, text, f) <= max_width:
            return f
    return _font(minimum, bold)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, max_width: int, max_lines: int) -> list[str]:
    text = _clean(text)
    if not text:
        return []
    words = text.split(" ")
    lines: list[str] = []
    cur = ""
    used = 0
    for idx, word in enumerate(words):
        trial = word if not cur else cur + " " + word
        if _text_width(draw, trial, font) <= max_width:
            cur = trial
            used = idx + 1
            continue
        if cur:
            lines.append(cur)
        cur = word
        used = idx + 1
        if len(lines) >= max_lines - 1:
            break
    if cur and len(lines) < max_lines:
        lines.append(cur)
    if used < len(words) and lines:
        last = lines[-1]
        while last and _text_width(draw, last + "…", font) > max_width:
            last = last[:-1]
        lines[-1] = last.rstrip() + "…"
    return lines[:max_lines]


# EAN-13 bit patterns. We use EAN-13 whenever the incoming 13-digit product
# barcode has a valid check digit; otherwise we fall back to Code128.
_EAN_L = {
    "0": "0001101", "1": "0011001", "2": "0010011", "3": "0111101", "4": "0100011",
    "5": "0110001", "6": "0101111", "7": "0111011", "8": "0110111", "9": "0001011",
}
_EAN_G = {
    "0": "0100111", "1": "0110011", "2": "0011011", "3": "0100001", "4": "0011101",
    "5": "0111001", "6": "0000101", "7": "0010001", "8": "0001001", "9": "0010111",
}
_EAN_R = {
    "0": "1110010", "1": "1100110", "2": "1101100", "3": "1000010", "4": "1011100",
    "5": "1001110", "6": "1010000", "7": "1000100", "8": "1001000", "9": "1110100",
}
_EAN_PARITY = {
    "0": "LLLLLL", "1": "LLGLGG", "2": "LLGGLG", "3": "LLGGGL", "4": "LGLLGG",
    "5": "LGGLLG", "6": "LGGGLL", "7": "LGLGLG", "8": "LGLGGL", "9": "LGGLGL",
}


def _valid_ean13(value: str) -> bool:
    if len(value) != 13 or not value.isdigit():
        return False
    total = sum(int(value[i]) * (1 if i % 2 == 0 else 3) for i in range(12))
    check = (10 - total % 10) % 10
    return check == int(value[-1])


def _draw_ean13(draw: ImageDraw.ImageDraw, value: str, *, left: int, top: int, width: int, height: int) -> None:
    parity = _EAN_PARITY[value[0]]
    bits = "101"
    for i, digit in enumerate(value[1:7]):
        bits += (_EAN_L if parity[i] == "L" else _EAN_G)[digit]
    bits += "01010"
    for digit in value[7:]:
        bits += _EAN_R[digit]
    bits += "101"

    # 95 encoded modules plus at least 11 quiet modules on each side.
    module = max(2, min(4, width // (95 + 24)))
    encoded_w = 95 * module
    x0 = left + (width - encoded_w) // 2
    guard_ranges = [(0, 2), (45, 49), (92, 94)]
    for i, bit in enumerate(bits):
        if bit != "1":
            continue
        guard = any(a <= i <= b for a, b in guard_ranges)
        extra = 7 if guard else 0
        draw.rectangle((x0 + i * module, top, x0 + (i + 1) * module - 1, top + height + extra), fill=0)


def _draw_code128(draw: ImageDraw.ImageDraw, value: str, *, left: int, top: int, width: int, height: int) -> None:
    barcode = code128.Code128(value, barHeight=1, barWidth=1, humanReadable=False)
    barcode.validate()
    barcode.encode()
    pattern = barcode.decompose()
    modules = 0
    for ch in pattern:
        modules += (ord(ch) - (ord("A") - 1)) if ch.isupper() else (ord(ch) - (ord("a") - 1))
    quiet_modules = 12
    module = max(2, min(3, width // max(1, modules + quiet_modules * 2)))
    encoded_w = modules * module
    x = left + (width - encoded_w) // 2
    for ch in pattern:
        count = (ord(ch) - (ord("A") - 1)) if ch.isupper() else (ord(ch) - (ord("a") - 1))
        w = count * module
        if ch.isupper():
            draw.rectangle((x, top, x + w - 1, top + height), fill=0)
        x += w


def _draw_product_barcode(draw: ImageDraw.ImageDraw, value: str, *, left: int, top: int, width: int, height: int) -> None:
    if _valid_ean13(value):
        _draw_ean13(draw, value, left=left, top=top, width=width, height=height)
    else:
        _draw_code128(draw, value, left=left, top=top, width=width, height=height)


def _label_canvas(width_mm: float = DEFAULT_WIDTH_MM, height_mm: float = DEFAULT_HEIGHT_MM) -> Image.Image:
    return Image.new("L", (_px(width_mm), _px(height_mm)), 255)


def product_barcode_label_png(
    barcode: str,
    article: str | None = None,
    size: str | None = None,
    width_mm: float = DEFAULT_WIDTH_MM,
    height_mm: float = DEFAULT_HEIGHT_MM,
    *,
    title: str | None = None,
    brand: str | None = None,
    composition: str | None = None,
    color: str | None = None,
    country: str | None = None,
    scale_percent: float = 100.0, offset_x_mm: float = 0.0, offset_y_mm: float = 0.0,
) -> bytes:
    """Raster product label in *final printer dots* (58x40mm => 464x320).

    No internal rotation is used. The image orientation is the physical media orientation:
    58 mm across the print head and 40 mm in the feed direction. The Print Agent sends these
    dots directly with TSPL BITMAP, bypassing PDF and Windows page scaling completely.
    """
    barcode = _clean(barcode)
    if not barcode:
        raise ValueError("Не указан штрихкод товара")

    img = _label_canvas(width_mm, height_mm)
    draw = ImageDraw.Draw(img)
    W, H = img.size
    margin = 12  # 1.5 mm at 8 dots/mm
    right = W - margin

    # Barcode owns the bottom 98 dots so it always stays large and scanner-readable.
    barcode_top = H - 96
    barcode_h = 61
    number_y = H - 27
    text_bottom = barcode_top - 8

    size_text = _clean(size)
    size_box_w = 104 if size_text else 0
    size_box_h = 78 if size_text else 0
    gap = 10 if size_text else 0
    main_right = right - size_box_w - gap
    main_w = max(160, main_right - margin)

    y = 9
    title_text = _clean(title) or "Товар"
    title_font = _font(22, True)
    title_lines = _wrap(draw, title_text, title_font, main_w, 2)
    for line in title_lines:
        draw.text((margin, y), line, fill=0, font=title_font)
        y += 26

    if size_text:
        bx = right - size_box_w
        by = 10
        draw.rounded_rectangle((bx, by, right, by + size_box_h), radius=4, outline=0, width=2)
        sf = _fit_font(draw, size_text, size_box_w - 10, 30, 17, bold=True)
        sb = draw.textbbox((0, 0), size_text, font=sf)
        sw, sh = sb[2] - sb[0], sb[3] - sb[1]
        draw.text((bx + (size_box_w - sw) // 2, by + 10), size_text, fill=0, font=sf)
        cap = "Размер"
        cf = _font(13, False)
        cw = _text_width(draw, cap, cf)
        draw.text((bx + (size_box_w - cw) // 2, by + size_box_h - 22), cap, fill=0, font=cf)

    def info(label: str, value: str | None, *, bold_value: bool = False, max_lines: int = 1):
        nonlocal y
        value = _clean(value)
        if not value or y >= text_bottom:
            return
        text = f"{label}: {value}" if label else value
        f = _font(16 if not bold_value else 17, bold_value)
        lines = _wrap(draw, text, f, main_w, max_lines)
        for line in lines:
            if y + 18 > text_bottom:
                return
            draw.text((margin, y), line, fill=0, font=f)
            y += 19

    info("Арт.", article, bold_value=True)
    info("Бренд", brand)
    info("Цвет", color)
    info("Состав", composition, max_lines=2)
    info("Страна", country)

    # A thin separator makes the barcode zone visually stable and prevents text drift.
    draw.line((margin, barcode_top - 5, right, barcode_top - 5), fill=0, width=1)
    _draw_product_barcode(draw, barcode, left=margin + 2, top=barcode_top, width=W - 2 * margin - 4, height=barcode_h)
    nf = _fit_font(draw, barcode, W - 2 * margin, 20, 15, bold=True)
    nw = _text_width(draw, barcode, nf)
    draw.text(((W - nw) // 2, number_y), barcode, fill=0, font=nf)

    # Optional fine calibration is applied in printer dots, not PDF points.
    scale = max(0.92, min(1.04, float(scale_percent) / 100.0))
    ox = _px(abs(offset_x_mm)) * (1 if offset_x_mm >= 0 else -1) if offset_x_mm else 0
    oy = _px(abs(offset_y_mm)) * (-1 if offset_y_mm >= 0 else 1) if offset_y_mm else 0
    if abs(scale - 1.0) > 0.001 or ox or oy:
        cw, ch = max(1, int(round(W * scale))), max(1, int(round(H * scale)))
        content = img.resize((cw, ch), Image.Resampling.NEAREST)
        calibrated = Image.new("L", (W, H), 255)
        calibrated.paste(content, ((W - cw) // 2 + ox, (H - ch) // 2 + oy))
        img = calibrated

    out = BytesIO()
    img.save(out, format="PNG", optimize=False)
    return out.getvalue()


def wb_sticker_exact_png(
    raw: bytes,
    width_mm: float = DEFAULT_WIDTH_MM,
    height_mm: float = DEFAULT_HEIGHT_MM,
    *,
    rotate: int = -1,
    scale_percent: float = 100.0,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> bytes:
    """Normalize a WB PNG to exact XP-420B dots without PDF.

    WB is already requested as 58x40 PNG. If its aspect is correct, it fills the whole label.
    If WB returns a different orientation, auto-rotation is applied. Resampling is nearest-neighbour
    so QR/DataMatrix/barcode edges remain hard instead of becoming grey/blurred.
    """
    target = _label_canvas(width_mm, height_mm)
    W, H = target.size
    source = Image.open(BytesIO(raw)).convert("L")

    actual_rotate = int(rotate)
    if actual_rotate < 0:
        source_landscape = source.width >= source.height
        target_landscape = W >= H
        actual_rotate = 90 if source_landscape != target_landscape else 0
    actual_rotate %= 360
    if actual_rotate:
        # PIL positive angle is counter-clockwise. expand=True keeps all source pixels.
        source = source.rotate(actual_rotate, expand=True, fillcolor=255)

    scale = max(0.90, min(1.05, float(scale_percent) / 100.0))
    # Small fixed safety inset: just 2 printer dots, not millimetres. This avoids clipping
    # the first/last module while still letting WB occupy essentially the entire label.
    avail_w = max(1, int((W - 4) * scale))
    avail_h = max(1, int((H - 4) * scale))
    ratio_src = source.width / max(1, source.height)
    ratio_dst = avail_w / max(1, avail_h)
    if abs(ratio_src - ratio_dst) / ratio_dst < 0.035:
        resized = source.resize((avail_w, avail_h), Image.Resampling.NEAREST)
    else:
        resized = ImageOps.contain(source, (avail_w, avail_h), method=Image.Resampling.NEAREST)

    x = (W - resized.width) // 2 + _px(offset_x_mm)
    y = (H - resized.height) // 2 - _px(offset_y_mm)
    target.paste(resized, (x, y))
    # Hard threshold prevents anti-aliased grey edges from reducing scanner contrast.
    target = target.point(lambda p: 0 if p < 190 else 255, mode="1")

    out = BytesIO()
    target.save(out, format="PNG", optimize=False)
    return out.getvalue()



def supply_box_label_png(
    raw_barcode_png: bytes,
    *,
    item_count: int | None = None,
    width_mm: float = DEFAULT_WIDTH_MM,
    height_mm: float = DEFAULT_HEIGHT_MM,
    scale_percent: float = 100.0,
    offset_x_mm: float = 0.0,
    offset_y_mm: float = 0.0,
) -> bytes:
    """Build a 58x40 supply/box label for direct TSPL printing.

    Open supply: print only the official WB supply barcode, full-size.
    Closed supply: keep the same barcode large and reserve a small bottom line
    for the item count.  The white background / hard black modules match the
    already-working direct-TSPL label path.
    """
    if item_count is None:
        return wb_sticker_exact_png(
            raw_barcode_png,
            width_mm=width_mm,
            height_mm=height_mm,
            rotate=-1,
            scale_percent=scale_percent,
            offset_x_mm=offset_x_mm,
            offset_y_mm=offset_y_mm,
        )

    target = _label_canvas(width_mm, height_mm)
    W, H = target.size
    source = Image.open(BytesIO(raw_barcode_png)).convert("L")

    # Match source orientation to the physical media before fitting it.
    if (source.width >= source.height) != (W >= H):
        source = source.rotate(90, expand=True, fillcolor=255)

    # Keep only ~5 mm for the quantity line so the WB barcode stays large.
    footer_h = max(34, _px(5.0))
    sep_y = H - footer_h
    scale = max(0.90, min(1.05, float(scale_percent) / 100.0))
    avail_w = max(1, int((W - 4) * scale))
    avail_h = max(1, int((sep_y - 4) * scale))
    resized = ImageOps.contain(source, (avail_w, avail_h), method=Image.Resampling.NEAREST)
    x = (W - resized.width) // 2 + _px(offset_x_mm)
    y = (sep_y - resized.height) // 2 - _px(offset_y_mm)
    target.paste(resized, (x, y))

    draw = ImageDraw.Draw(target)
    draw.line((8, sep_y, W - 9, sep_y), fill=0, width=1)
    text = f"Товаров: {max(0, int(item_count))} шт."
    font = _fit_font(draw, text, W - 20, 22, 15, bold=True)
    tw = _text_width(draw, text, font)
    tb = draw.textbbox((0, 0), text, font=font)
    th = tb[3] - tb[1]
    draw.text(((W - tw) // 2, sep_y + max(2, (footer_h - th) // 2 - 1)), text, fill=0, font=font)

    target = target.point(lambda px: 0 if px < 190 else 255, mode="1")
    out = BytesIO()
    target.save(out, format="PNG", optimize=False)
    return out.getvalue()

def print_calibration_png(kind: str = "product", width_mm: float = DEFAULT_WIDTH_MM, height_mm: float = DEFAULT_HEIGHT_MM,
                          *, scale_percent: float = 100.0, offset_x_mm: float = 0.0, offset_y_mm: float = 0.0) -> bytes:
    if kind == "product":
        return product_barcode_label_png(
            "2051090535895", "183321", "M-L 48-50", width_mm=width_mm, height_mm=height_mm,
            title="Костюм мужской графитовый", brand="TM LIMITED",
            composition="50% вискоза; 28% полиэстер; 22% нейлон", color="графитовый", country="Киргизия",
            scale_percent=scale_percent, offset_x_mm=offset_x_mm, offset_y_mm=offset_y_mm,
        )

    img = _label_canvas(width_mm, height_mm)
    draw = ImageDraw.Draw(img)
    W, H = img.size
    draw.rectangle((2, 2, W - 3, H - 3), outline=0, width=2)
    title = f"WB TEST {width_mm:g}x{height_mm:g}"
    f1 = _font(23, True)
    tw = _text_width(draw, title, f1)
    draw.text(((W - tw) // 2, 12), title, fill=0, font=f1)
    f2 = _font(14, False)
    sub = "Печать 1:1 • без PDF • 203 DPI"
    sw = _text_width(draw, sub, f2)
    draw.text(((W - sw) // 2, 45), sub, fill=0, font=f2)
    draw.line((W // 2, 72, W // 2, 100), fill=0, width=1)
    draw.line((W // 2 - 14, 86, W // 2 + 14, 86), fill=0, width=1)
    _draw_product_barcode(draw, "57093452429", left=20, top=125, width=W - 40, height=92)
    n = "57093452429"
    nf = _font(22, True)
    nw = _text_width(draw, n, nf)
    draw.text(((W - nw) // 2, 235), n, fill=0, font=nf)
    note = "Рамка должна быть целиком внутри одной этикетки"
    nn = _fit_font(draw, note, W - 24, 14, 11)
    nw2 = _text_width(draw, note, nn)
    draw.text(((W - nw2) // 2, H - 27), note, fill=0, font=nn)
    scale = max(0.92, min(1.04, float(scale_percent) / 100.0))
    ox = _px(abs(offset_x_mm)) * (1 if offset_x_mm >= 0 else -1) if offset_x_mm else 0
    oy = _px(abs(offset_y_mm)) * (-1 if offset_y_mm >= 0 else 1) if offset_y_mm else 0
    if abs(scale - 1.0) > 0.001 or ox or oy:
        cw, ch = max(1, int(round(W * scale))), max(1, int(round(H * scale)))
        content = img.resize((cw, ch), Image.Resampling.NEAREST)
        calibrated = Image.new("L", (W, H), 255)
        calibrated.paste(content, ((W - cw) // 2 + ox, (H - ch) // 2 + oy))
        img = calibrated
    out = BytesIO(); img.save(out, format="PNG", optimize=False); return out.getvalue()
