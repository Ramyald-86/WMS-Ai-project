"""Human-readable pallet labels: large section + public PAL code, QR remains unique."""
from __future__ import annotations
import re
from io import BytesIO
from pathlib import Path
from app.models import Location
from app.services.r145f25_places import public_code

_installed=False

def _section_number(value):
    raw=str(value or '').strip()
    raw=re.sub(r'^SEC[-_ ]*','',raw,flags=re.I).strip()
    if raw.isdigit(): return str(int(raw))
    m=re.search(r'(\d+)',raw)
    return str(int(m.group(1))) if m else raw

def _decorate(db, rows):
    by_id={str(x.id):x for x in rows}
    # Fetching parents lazily with db.get is bounded by label count and avoids changing schema.
    for loc in rows:
        if str(loc.location_type or '').upper() not in ('PALLET','PALLET_PLACE'):
            continue
        cur=loc;section=None
        for _ in range(8):
            if not cur:break
            if str(cur.location_type or '').upper()=='SECTION':section=cur;break
            cur=db.get(Location,cur.parent_id) if cur.parent_id else None
        setattr(loc,'_fc27_public_code',public_code(loc))
        setattr(loc,'_fc27_section',_section_number((section.code if section else '') or (section.name if section else '')))
    return rows

def _is_pallet(loc): return str(getattr(loc,'location_type','') or '').upper() in ('PALLET','PALLET_PLACE')
def _meta(loc): return str(getattr(loc,'_fc27_section','') or ''),str(getattr(loc,'_fc27_public_code','') or public_code(loc))

def install_label_patch():
    global _installed
    if _installed:return
    from app.routers import layout as legacy
    original_rows=legacy._location_label_rows
    original_draw=legacy._draw_location_label
    original_png=legacy._location_label_png

    def rows(db,**kwargs): return _decorate(db,original_rows(db,**kwargs))

    def draw_pdf(c,location,page_w,page_h):
        if not _is_pallet(location):return original_draw(c,location,page_w,page_h)
        section,pal=_meta(location)
        if not section:return original_draw(c,location,page_w,page_h)
        value=str(location.barcode or location.code or '').strip()
        if not value:return original_draw(c,location,page_w,page_h)
        from reportlab.lib.units import mm
        from reportlab.graphics.barcode import createBarcodeDrawing
        _,bold=legacy._location_label_fonts()
        # Left: huge section number. Right: public pallet name and unique QR.
        left_w=20*mm;right_x=21*mm;right_w=page_w-right_x-2*mm
        c.setFont(bold,8);c.drawCentredString(left_w/2,page_h-5*mm,'СЕКЦИЯ')
        size=legacy._fit_label_text(section,bold,left_w-2*mm,36,18);c.setFont(bold,size);c.drawCentredString(left_w/2,page_h/2-5*mm,section)
        title_size=legacy._fit_label_text(pal,bold,right_w,18,9);c.setFont(bold,title_size);c.drawCentredString(right_x+right_w/2,page_h-6*mm,pal)
        qr_size=min(right_w-2*mm,page_h-12*mm)
        qr=createBarcodeDrawing('QR',value=value,width=qr_size,height=qr_size,barLevel='M')
        qr.drawOn(c,right_x+(right_w-qr_size)/2,2*mm)

    def draw_png(location,width_mm,height_mm):
        if not _is_pallet(location):return original_png(location,width_mm,height_mm)
        section,pal=_meta(location)
        if not section:return original_png(location,width_mm,height_mm)
        value=str(location.barcode or location.code or '').strip()
        if not value:return original_png(location,width_mm,height_mm)
        from PIL import Image,ImageDraw,ImageFont
        dots=8;width=max(1,int(round(float(width_mm)*dots)));height=max(1,int(round(float(height_mm)*dots)))
        image=Image.new('L',(width,height),255);draw=ImageDraw.Draw(image);font_path=Path('/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf')
        def font_fit(text,maxw,start,minv):
            for size in range(start,minv-1,-1):
                try:f=ImageFont.truetype(str(font_path),size) if font_path.exists() else ImageFont.load_default()
                except Exception:f=ImageFont.load_default()
                b=draw.textbbox((0,0),text,font=f)
                if b[2]-b[0]<=maxw:return f
            return ImageFont.load_default()
        left=int(20*dots);right_x=int(21*dots);right_w=width-right_x-int(2*dots)
        fsmall=font_fit('СЕКЦИЯ',left-int(2*dots),22,12);b=draw.textbbox((0,0),'СЕКЦИЯ',font=fsmall);draw.text(((left-(b[2]-b[0]))//2,int(1*dots)),'СЕКЦИЯ',font=fsmall,fill=0)
        fsec=font_fit(section,left-int(2*dots),96,42);b=draw.textbbox((0,0),section,font=fsec);draw.text(((left-(b[2]-b[0]))//2,(height-(b[3]-b[1]))//2),section,font=fsec,fill=0)
        fpal=font_fit(pal,right_w,48,22);b=draw.textbbox((0,0),pal,font=fpal);draw.text((right_x+(right_w-(b[2]-b[0]))//2,int(1*dots)),pal,font=fpal,fill=0)
        from reportlab.graphics.barcode.qr import QrCodeWidget
        widget=QrCodeWidget(value=value,barLevel='M');widget.qr.make();modules=widget.qr.modules or [];count=int(widget.qr.getModuleCount() or 0)
        max_q=min(right_w-int(2*dots),height-int(11*dots));border=2;module_px=max(1,max_q//(count+border*2));qr_px=(count+border*2)*module_px
        q=Image.new('L',(qr_px,qr_px),255);qd=ImageDraw.Draw(q)
        for ri,row in enumerate(modules):
            for ci,dark in enumerate(row):
                if dark:
                    x0=(ci+border)*module_px;y0=(ri+border)*module_px;qd.rectangle((x0,y0,x0+module_px-1,y0+module_px-1),fill=0)
        image.paste(q,(right_x+(right_w-q.width)//2,height-q.height-int(1*dots)))
        out=BytesIO();image.save(out,format='PNG',optimize=False);return out.getvalue()

    legacy._location_label_rows=rows
    legacy._draw_location_label=draw_pdf
    legacy._location_label_png=draw_png
    _installed=True
