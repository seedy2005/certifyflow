"""Certificate rendering shared by previews and batch generation."""
from pathlib import Path
from io import BytesIO
import re
import hashlib
import urllib.parse
import urllib.request
from PIL import Image, ImageFont
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from fontTools.ttLib import TTFont as FontToolsTTFont
from reportlab.lib.colors import HexColor
from reportlab.lib.utils import ImageReader
from pypdf import PdfReader, PdfWriter

FONT_DIR = Path(__file__).parent / 'fonts'
FONT_DIR.mkdir(exist_ok=True)

# Keep a little margin under a decimal megabyte so the limit is unambiguous.
MAX_CERTIFICATE_BYTES = 1_000_000

def compress_pdf(path: Path, max_bytes: int = MAX_CERTIFICATE_BYTES) -> int:
    """Optimize a generated PDF and enforce the per-certificate size limit.

    Text and vector artwork stay as PDF vectors. Embedded images are progressively
    recompressed/resampled only as much as needed to meet the size limit.
    """
    import pymupdf

    original = path.with_name(path.name + '.uncompressed')
    candidate = path.with_name(path.name + '.compressed.pdf')
    path.replace(original)
    try:
        # Start with high quality, then lower image resolution and JPEG quality
        # only if the optimized PDF still exceeds the per-file limit.
        settings = (
            (220, 180, 82),
            (180, 144, 72),
            (144, 110, 62),
            (110, 84, 50),
            (84, 64, 40),
        )
        size = original.stat().st_size
        for dpi_threshold, dpi_target, quality in settings:
            with pymupdf.open(original) as document:
                document.rewrite_images(
                    dpi_threshold=dpi_threshold,
                    dpi_target=dpi_target,
                    quality=quality,
                )
                document.save(
                    candidate,
                    garbage=4,
                    deflate=True,
                    deflate_images=True,
                    deflate_fonts=True,
                    use_objstms=True,
                )

            size = candidate.stat().st_size
            if size < max_bytes:
                candidate.replace(path)
                return size
            candidate.unlink(missing_ok=True)

        raise ValueError(
            f'Certificate is {size:,} bytes after maximum compression; '
            f'each certificate must be smaller than {max_bytes:,} bytes. '
            'Use a smaller or lower-resolution template.'
        )
    finally:
        candidate.unlink(missing_ok=True)
        if original.exists():
            # Preserve the rendered PDF if compression fails for any reason.
            if not path.exists():
                original.replace(path)
            else:
                original.unlink(missing_ok=True)

def _font(name):
    if name not in {'Helvetica','Times-Roman','Courier'} and Path(name).suffix.lower() not in {'.ttf','.otf'}:
        return name
    if Path(name).name != name or name in {'.','..'}: return 'Helvetica'
    candidate = FONT_DIR / name
    if candidate.is_file() and candidate.suffix.lower() in {'.ttf','.otf'}:
        registered = 'user_' + re.sub(r'\W','_',candidate.stem)
        if registered not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont(registered, str(candidate)))
        return registered
    return name if name in pdfmetrics.getRegisteredFontNames() else 'Helvetica'

def _google_font(name, bold=False, italic=False):
    weight=700 if bold else 400
    cache_name=(re.sub(r'[^A-Za-z0-9_-]+','_',name).strip('_')[:60] or 'GoogleFont')+'_'+hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]
    path=FONT_DIR/f'google_{cache_name}_{weight}_{int(italic)}.ttf'
    registered=f'google_{cache_name}_{weight}_{int(italic)}'
    if not path.exists():
        style=f"ital,wght@{int(italic)},{weight}"
        query=urllib.parse.urlencode({'family':f'{name}:{style}','display':'swap'})
        req=urllib.request.Request('https://fonts.googleapis.com/css2?'+query,headers={'User-Agent':'Mozilla/5.0 (compatible; MSIE 9.0; Windows NT 6.1; Trident/5.0)'})
        with urllib.request.urlopen(req,timeout=15) as response: css=response.read().decode('utf-8')
        urls=re.findall(r'src:\s*url\((https://[^)]+)\)',css)
        if not urls: raise ValueError('Google Fonts returned no font file; check the selected family and internet access')
        # Google CSS lists script subsets, with the broad Latin subset last. FontTools converts
        # the legacy WOFF response into TTF for ReportLab embedding.
        font_req=urllib.request.Request(urls[-1],headers={'User-Agent':'CertifyFlow/1.0'})
        with urllib.request.urlopen(font_req,timeout=20) as response: payload=response.read(20_000_000)
        if payload.startswith(b'\x00\x01\x00\x00'):
            path.write_bytes(payload)
        else:
            source=FontToolsTTFont(BytesIO(payload)); source.flavor=None; source.save(str(path))
        if not path.read_bytes().startswith(b'\x00\x01\x00\x00'): raise ValueError('Google Fonts returned an unsupported font format')
    if registered not in pdfmetrics.getRegisteredFontNames(): pdfmetrics.registerFont(TTFont(registered,str(path)))
    return registered

def _styled_font(name, bold=False, italic=False):
    builtins={
        ('Helvetica',False,False):'Helvetica',('Helvetica',True,False):'Helvetica-Bold',('Helvetica',False,True):'Helvetica-Oblique',('Helvetica',True,True):'Helvetica-BoldOblique',
        ('Times-Roman',False,False):'Times-Roman',('Times-Roman',True,False):'Times-Bold',('Times-Roman',False,True):'Times-Italic',('Times-Roman',True,True):'Times-BoldItalic',
        ('Courier',False,False):'Courier',('Courier',True,False):'Courier-Bold',('Courier',False,True):'Courier-Oblique',('Courier',True,True):'Courier-BoldOblique'}
    if name in {'Helvetica','Times-Roman','Courier'}: return builtins[(name,bold,italic)]
    candidate=FONT_DIR/name
    if candidate.is_file(): return _font(name)
    if name in pdfmetrics.getRegisteredFontNames(): return name
    return _google_font(name,bold,italic)

def _measure(text, font, size):
    return pdfmetrics.stringWidth(text, font, size)

def _draw_fields(c, fields, data, width, height):
    warnings=[]
    for field in fields:
        value = field.get('fixed_text') or data.get(field['key']) or ''
        value = str(value)
        if not value:
            if field.get('required'): warnings.append(f"{field['key']}: required value is empty")
            continue
        try: font = _styled_font(field.get('font_family','Helvetica'),field.get('bold',False),field.get('italic',False))
        except Exception as exc:
            warnings.append(f"{field['key']}: could not load font: {exc}")
            continue
        size=float(field.get('font_size',30)); min_size=float(field.get('min_font_size',12))
        maxw=width*float(field.get('max_width',.8))
        rendered=value
        # Built-in PDF fonts only support Latin-1. Report unsupported glyphs rather than corrupting names.
        try:
            if font == 'Helvetica': rendered.encode('cp1252')
            else:
                face=pdfmetrics.getFont(font).face
                if hasattr(face,'charWidths') and any(ord(ch) not in face.charWidths for ch in rendered):
                    raise UnicodeEncodeError('font',rendered,0,1,'missing glyph')
        except (UnicodeEncodeError,KeyError):
            warnings.append(f"{field['key']}: selected font cannot render all characters")
            continue
        if _measure(rendered,font,size)>maxw and _measure(rendered,font,min_size)<=maxw:
            # Find the largest fitting point size without a per-point loop. This remains fast
            # for large configured sizes and keeps the configured value intact in storage.
            low,high=min_size,size
            while high-low>.1:
                mid=(low+high)/2
                if _measure(rendered,font,mid)<=maxw: low=mid
                else: high=mid
            size=low
        if _measure(rendered,font,size)>maxw:
            warnings.append(f"{field['key']}: text exceeds maximum width at minimum font size")
        x=float(field['x'])*width; y=(1-float(field['y']))*height
        y -= (pdfmetrics.getAscent(font,size)+pdfmetrics.getDescent(font,size))/2
        align=field.get('alignment','center')
        c.setFont(font,size); c.setFillColor(HexColor(field.get('color','#18392F')))
        # Normalized point is the visual center of the text box, adjusted using font metrics.
        text_width=_measure(rendered,font,size)
        if align=='left': left=x; c.drawString(x,y,rendered)
        elif align=='right': left=x-text_width; c.drawRightString(x,y,rendered)
        else: left=x-text_width/2; c.drawCentredString(x,y,rendered)
        if left<0 or left+text_width>width:
            warnings.append(f"{field['key']}: text extends beyond the page horizontally")
        ascent=pdfmetrics.getAscent(font,size); descent=pdfmetrics.getDescent(font,size)
        if y+descent<0 or y+ascent>height:
            warnings.append(f"{field['key']}: text extends beyond the page vertically")
        if field.get('underline'):
            c.saveState(); c.setStrokeColor(HexColor(field.get('color','#18392F'))); c.setLineWidth(max(.5,size*.035)); c.line(left,y-2,left+text_width,y-2); c.restoreState()
    return warnings

def render_pdf(source: Path, dest: Path, page_index: int, fields: list, data: dict):
    reader=PdfReader(str(source), strict=True)
    if reader.is_encrypted: raise ValueError('Encrypted PDFs are not supported')
    if page_index >= len(reader.pages): raise ValueError('Selected PDF page no longer exists')
    page=reader.pages[page_index]
    if page.get('/Rotate',0): page.transfer_rotation_to_content()
    box=page.cropbox; width=float(box.width); height=float(box.height)
    if width<=0 or height<=0: raise ValueError('PDF page has invalid dimensions')
    mem=BytesIO(); c=canvas.Canvas(mem,pagesize=(width,height),pageCompression=1)
    warnings=_draw_fields(c,fields,data,width,height); c.showPage(); c.save(); mem.seek(0)
    overlay=PdfReader(mem).pages[0]
    # Crop-box offsets are respected by translating normalized points into page coordinates.
    if float(box.left) or float(box.bottom):
        from pypdf import Transformation
        overlay.add_transformation(Transformation().translate(tx=float(box.left),ty=float(box.bottom)))
    page.merge_page(overlay)
    writer=PdfWriter(); writer.add_page(page)
    with dest.open('wb') as f: writer.write(f)
    return warnings

def render_image(source: Path, dest: Path, fields: list, data: dict):
    with Image.open(source) as im:
        im=im.convert('RGB')
        width,height=im.size
        mem=BytesIO(); c=canvas.Canvas(mem,pagesize=(width*.75,height*.75),pageCompression=1)
        c.drawImage(ImageReader(im),0,0,width=width*.75,height=height*.75)
        warnings=_draw_fields(c,fields,data,width*.75,height*.75); c.showPage(); c.save(); mem.seek(0)
        reader=PdfReader(mem); writer=PdfWriter(); writer.add_page(reader.pages[0])
        with dest.open('wb') as f: writer.write(f)
        return warnings

def render(source: Path, source_type: str, dest: Path, page_index: int, fields: list, data: dict):
    return render_pdf(source,dest,page_index,fields,data) if source_type=='pdf' else render_image(source,dest,fields,data)
