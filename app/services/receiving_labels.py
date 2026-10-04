import io
from pathlib import Path


HARD_LABEL_WIDTH_MM = 58.0
HARD_LABEL_HEIGHT_MM = 40.0
HARD_LABEL_DPI = 203


def _label_font_paths():
    root = Path(__file__).resolve().parents[1] / 'static' / 'fc_r69_fonts'
    regular = root / 'DejaVuSans.ttf'
    bold = root / 'DejaVuSans-Bold.ttf'
    if not regular.is_file():
        regular = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')
    if not bold.is_file():
        bold = Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
    return regular, bold


def _fit_pil_font(draw, text, path, max_size, min_size, max_width):
    from PIL import ImageFont
    text = str(text or '')
    for size in range(int(max_size), int(min_size) - 1, -1):
        font = ImageFont.truetype(str(path), size)
        if draw.textlength(text, font=font) <= max_width:
            return font
    return ImageFont.truetype(str(path), int(min_size))


def _split_label_title(draw, text, path, max_width):
    """Prefer one large line; if it would become tiny, split into two readable lines."""
    value = str(text or '').strip()
    one = _fit_pil_font(draw, value, path, 44, 22, max_width)
    if draw.textlength(value, font=one) <= max_width and one.size >= 28:
        return [value], one
    # Split on warehouse-friendly separators while keeping exact visible text.
    candidates = []
    for sep in ('_', '-', ' ', '/'):
        positions = [i for i, ch in enumerate(value) if ch == sep]
        for i in positions:
            left, right = value[:i + 1].strip(), value[i + 1:].strip()
            if left and right:
                candidates.append((left, right))
    if candidates:
        left, right = min(candidates, key=lambda x: abs(len(x[0]) - len(x[1])))
    else:
        mid = max(1, len(value) // 2)
        left, right = value[:mid], value[mid:]
    two = _fit_pil_font(draw, max((left, right), key=len), path, 32, 20, max_width)
    return [left, right], two


def _wrap_label_lines(draw, text, path, max_width, max_lines=4, max_size=42, min_size=22):
    from PIL import ImageFont
    value = str(text or '').strip()
    if not value:
        return ['КОРОБ'], ImageFont.truetype(str(path), max_size)
    # Prefer human separators, then fall back to character wrapping. The goal is not
    # typography perfection but a large warehouse-readable name on a 58x40 label.
    tokens=[]
    current=''
    for ch in value:
        current += ch
        if ch in ' _-/':
            tokens.append(current)
            current=''
    if current:
        tokens.append(current)
    for size in range(int(max_size), int(min_size)-1, -1):
        font=ImageFont.truetype(str(path), size)
        lines=[]; line=''
        for token in tokens or [value]:
            candidate=line+token
            if line and draw.textlength(candidate, font=font) > max_width:
                lines.append(line.rstrip())
                line=token.lstrip()
            else:
                line=candidate
        if line:
            lines.append(line.rstrip())
        # Hard-wrap any still-too-wide fragment.
        fixed=[]
        for line in lines:
            if draw.textlength(line, font=font) <= max_width:
                fixed.append(line); continue
            part=''
            for ch in line:
                if part and draw.textlength(part+ch, font=font) > max_width:
                    fixed.append(part); part=ch
                else:
                    part += ch
            if part: fixed.append(part)
        lines=[x for x in fixed if x]
        if lines and len(lines) <= max_lines:
            return lines, font
    font=ImageFont.truetype(str(path), int(min_size))
    # Last-resort deterministic wrapping, never silently shrink to unreadable text.
    lines=[]; part=''
    for ch in value:
        if part and draw.textlength(part+ch, font=font) > max_width:
            lines.append(part); part=ch
        else:
            part += ch
    if part: lines.append(part)
    if len(lines) > max_lines:
        joined=''.join(lines)
        approx=max(1, len(joined)//max_lines)
        lines=[joined[i:i+approx] for i in range(0,len(joined),approx)][:max_lines]
        if sum(len(x) for x in lines) < len(joined):
            lines[-1]=lines[-1][:-1]+'…'
    return lines, font


def box_agent_png(code, width_mm=58, height_mm=40, footer_text=None, display_name=None):
    """Native full-media 58x40 / 203 dpi QR label for Print Agent.

    r144 intentionally mirrors the proven location-label raster contract: a 464x320
    one-bit PNG with no DPI-resampling metadata.  Print Agent 1.1.8 sends every source
    pixel as one XPrinter dot, so the label cannot collapse into the upper-left corner.
    The public box name is large at the top and the QR fills the remaining media.
    """
    from PIL import Image, ImageDraw, ImageFont
    from reportlab.graphics.barcode.qrencoder import QRCode, QRErrorCorrectLevel

    value = str(code or '').strip()
    title = str(display_name or value or 'BOX').strip()
    footer = str(footer_text or '').strip()
    if not value or len(value) > 220 or any(ord(ch) < 32 for ch in value):
        raise ValueError('Некорректный QR короба')
    if not title or len(title) > 160 or any(ord(ch) < 32 for ch in title):
        raise ValueError('Некорректное название короба')

    dots = 8
    width = int(round(HARD_LABEL_WIDTH_MM * dots))   # 464
    height = int(round(HARD_LABEL_HEIGHT_MM * dots)) # 320
    image = Image.new('L', (width, height), 255)
    draw = ImageDraw.Draw(image)
    regular_path, bold_path = _label_font_paths()

    max_title_width = width - 28
    def font_for(text, start=50, minimum=24):
        for size in range(start, minimum - 1, -1):
            font = ImageFont.truetype(str(bold_path), size)
            b = draw.textbbox((0, 0), text, font=font)
            if b[2] - b[0] <= max_title_width:
                return font
        return ImageFont.truetype(str(bold_path), minimum)

    # One line whenever possible.  Long names get two large lines rather than tiny text.
    title_lines = [title]
    title_font = font_for(title)
    if title_font.size < 30:
        cuts = [i for i, ch in enumerate(title) if ch in ' _-/']
        if cuts:
            cut = min(cuts, key=lambda i: abs(i - len(title) / 2))
            a, b = title[:cut + 1].strip(), title[cut + 1:].strip()
            if a and b:
                title_lines = [a, b]
        if len(title_lines) == 1:
            mid = max(1, len(title) // 2)
            title_lines = [title[:mid], title[mid:]]
        title_font = font_for(max(title_lines, key=len), start=40, minimum=23)

    title_top = 4
    for line in title_lines[:2]:
        b = draw.textbbox((0, 0), line, font=title_font)
        tw = b[2] - b[0]
        draw.text(((width - tw) // 2, title_top), line, font=title_font, fill=0)
        title_top += title_font.size + 1

    footer_font = None
    footer_h = 0
    # Avoid wasting label height on a duplicate BOX/BRAK word when it is already in name.
    if footer and footer.casefold() not in title.casefold():
        footer_font = _fit_pil_font(draw, footer, regular_path, 22, 15, width - 24)
        footer_h = footer_font.size + 5

    # Same shape as the working cell labels: QR is centered and consumes nearly all
    # height below the title.  Four quiet modules are included inside the bitmap.
    qr = QRCode(None, QRErrorCorrectLevel.M)
    qr.addData(value)
    qr.make()
    quiet = 4
    modules = qr.modules or []
    n = len(modules)
    if not modules or n <= 0:
        raise ValueError('Не удалось сформировать QR короба')
    bottom = 7 + footer_h
    available_h = height - title_top - bottom
    qr_limit = min(width - 48, available_h)
    step = qr_limit // (n + quiet * 2)
    if step < 2:
        raise ValueError('QR короба не помещается на этикетке 58×40 мм')
    qr_px = (n + quiet * 2) * step
    left = (width - qr_px) // 2
    top = title_top + max(1, (available_h - qr_px) // 2)
    for yy, row in enumerate(modules):
        for xx, dark in enumerate(row):
            if dark:
                x = left + (xx + quiet) * step
                y = top + (yy + quiet) * step
                draw.rectangle((x, y, x + step - 1, y + step - 1), fill=0)

    if footer_font:
        b = draw.textbbox((0, 0), footer, font=footer_font)
        fw = b[2] - b[0]
        draw.text(((width - fw) // 2, height - footer_font.size - 2), footer, font=footer_font, fill=0)

    # Critical: exactly like location labels, send a hard 1-bit native-dot PNG and do
    # not embed physical DPI metadata. The agent calculates SIZE from pixel count / 8.
    image = image.point(lambda px: 0 if px < 190 else 255, mode='1')
    output = io.BytesIO()
    image.save(output, format='PNG', optimize=False)
    return output.getvalue()

def product_agent_png(barcode, article='', size='', name='', width_mm=58, height_mm=40):
    """Exact 58x40 mm / 203 dpi local product barcode for Print Agent."""
    from PIL import Image, ImageDraw
    from reportlab.graphics.barcode import createBarcodeDrawing
    from reportlab.graphics import renderPM
    from reportlab.lib.units import mm

    code = str(barcode or '').strip()
    article = str(article or '').strip()
    size = str(size or '').strip()
    name = str(name or '').strip()
    if not code or len(code) > 120 or any(ord(ch) < 32 for ch in code):
        raise ValueError('Некорректный ШК товара')

    width_mm = HARD_LABEL_WIDTH_MM
    height_mm = HARD_LABEL_HEIGHT_MM
    width = round(width_mm * HARD_LABEL_DPI / 25.4)
    height = round(height_mm * HARD_LABEL_DPI / 25.4)
    image = Image.new('RGB', (width, height), 'white')
    draw = ImageDraw.Draw(image)
    regular_path, bold_path = _label_font_paths()
    margin = 10

    title = ' · '.join(x for x in (article, size) if x) or 'Товар'
    title_font = _fit_pil_font(draw, title, bold_path, 38, 20, width - 2 * margin)
    title_y = 7
    draw.text(((width - draw.textlength(title, font=title_font)) / 2, title_y), title, font=title_font, fill='black')

    # Render Code128 at printer resolution. Start with a 0.30 mm module and shrink only
    # when a long barcode would exceed the safe 54 mm printable width.
    bar_width = 0.30 * mm
    bar_height = 14.5 * mm
    drawing = createBarcodeDrawing('Code128', value=code, barHeight=bar_height, barWidth=bar_width, humanReadable=False)
    max_w_pt = 54 * mm
    if drawing.width > max_w_pt:
        bar_width *= max_w_pt / drawing.width
        drawing = createBarcodeDrawing('Code128', value=code, barHeight=bar_height, barWidth=bar_width, humanReadable=False)
    raw = renderPM.drawToString(drawing, fmt='PNG', dpi=HARD_LABEL_DPI)
    barcode_image = Image.open(io.BytesIO(raw)).convert('RGB')
    if barcode_image.width > width - 2 * margin:
        target_w = width - 2 * margin
        target_h = max(70, round(barcode_image.height * target_w / barcode_image.width))
        barcode_image = barcode_image.resize((target_w, target_h), Image.Resampling.NEAREST)
    bx = (width - barcode_image.width) // 2
    by = 66
    image.paste(barcode_image, (bx, by))

    code_font = _fit_pil_font(draw, code, bold_path, 26, 16, width - 2 * margin)
    cy = min(height - 58, by + barcode_image.height + 6)
    draw.text(((width - draw.textlength(code, font=code_font)) / 2, cy), code, font=code_font, fill='black')

    if name:
        # One short identity line is useful on returns but must never force the barcode
        # onto a second physical label.
        short_name = name if len(name) <= 52 else name[:49] + '…'
        name_font = _fit_pil_font(draw, short_name, regular_path, 17, 12, width - 2 * margin)
        ny = height - name_font.size - 5
        draw.text(((width - draw.textlength(short_name, font=name_font)) / 2, ny), short_name, font=name_font, fill='black')

    output = io.BytesIO()
    image.save(output, format='PNG', dpi=(HARD_LABEL_DPI, HARD_LABEL_DPI))
    return output.getvalue()

def make_labels(payload,product=None):
    from reportlab.pdfgen import canvas
    from reportlab.lib.units import mm
    from reportlab.graphics.barcode import qr,code128
    from reportlab.graphics import renderPDF
    from reportlab.graphics.shapes import Drawing
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from pathlib import Path
    font='Helvetica'
    for path in (str(Path(__file__).resolve().parents[1]/'static/fc_r69_fonts/DejaVuSans.ttf'),'/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf','/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf'):
        if Path(path).is_file():
            if 'FCReceiving' not in pdfmetrics.getRegisteredFontNames():pdfmetrics.registerFont(TTFont('FCReceiving',path))
            font='FCReceiving';break
    result=io.BytesIO();pdf=canvas.Canvas(result,pagesize=(58*mm,40*mm))
    def centered(s,y,size=9):
        while pdfmetrics.stringWidth(s,font,size)>54*mm and size>4:size-=0.5
        pdf.setFont(font,size);pdf.drawCentredString(29*mm,y*mm,s)
    for index in range(payload.count):
        if payload.kind=='BOX':
            code=payload.code.strip() or f'BOX_{payload.label_date:%d%m%y}_{payload.batch}_{payload.start+index:03d}'
            if len(code)>120 or any(ord(c)<32 for c in code):raise ValueError('Некорректный код короба')
            widget=qr.QrCodeWidget(code);x,y,x2,y2=widget.getBounds();side=26*mm
            drawing=Drawing(side,side,transform=[side/(x2-x),0,0,side/(y2-y),0,0]);drawing.add(widget)
            renderPDF.draw(drawing, pdf, x=16*mm,y=11*mm)
            centered(code,6,8)
        elif payload.kind=='PRODUCT':
            if not product:raise ValueError('Товар не найден в справочнике WMS. Сначала добавьте артикул и размер')
            code=product['barcode'];barcode=code128.Code128(code,barHeight=16*mm,barWidth=.28*mm)
            pdf.saveState();pdf.translate(3*mm,13*mm);pdf.scale(min(1,52*mm/barcode.width),1);barcode.drawOn(pdf,0,0);pdf.restoreState()
            centered(str(product.get('article') or 'Товар')+' · '+str(product.get('size') or ''),33,10);centered(code,8,9)
        else:raise ValueError('Выберите этикетку короба или товара')
        pdf.showPage()
    pdf.save();return result.getvalue()
