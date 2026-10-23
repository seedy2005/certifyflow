from PIL import Image
from reportlab.pdfgen import canvas
from pypdf import PdfReader, PdfWriter
from app.rendering import render

def field(**overrides):
    return dict(key='participant_name',type='participant_name',x=.5,y=.5,alignment='center',font_family='Helvetica',font_size=30,min_font_size=12,color='#18392F',max_width=.8,required=True)|overrides

def test_png_centered_text_exports_page_dimensions_and_text(tmp_path):
    src=tmp_path/'bg.png'; Image.new('RGB',(800,600),'white').save(src); dest=tmp_path/'out.pdf'
    assert render(src,'image',dest,0,[field()],{'participant_name':'Ada Lovelace'})==[]
    page=PdfReader(dest).pages[0]
    assert round(float(page.mediabox.width))==600 and round(float(page.mediabox.height))==450
    assert 'Ada Lovelace' in page.extract_text()

def test_jpg_export_and_long_name_warning(tmp_path):
    src=tmp_path/'bg.jpg'; Image.new('RGB',(600,400),'white').save(src); dest=tmp_path/'out.pdf'
    warnings=render(src,'image',dest,0,[field(max_width=.2)],{'participant_name':'An exceptionally long participant name'})
    assert warnings and 'exceeds maximum width' in warnings[0]
    assert PdfReader(dest).pages

def test_pdf_vector_overlay_landscape_dimensions(tmp_path):
    src=tmp_path/'source.pdf'; c=canvas.Canvas(str(src),pagesize=(842,595)); c.drawString(10,10,'Background'); c.save()
    dest=tmp_path/'out.pdf'; render(src,'pdf',dest,0,[field()],{'participant_name':'Participant One'})
    page=PdfReader(dest).pages[0]
    assert round(float(page.mediabox.width))==842 and round(float(page.mediabox.height))==595
    assert 'Participant One' in page.extract_text()

def test_rotated_pdf_uses_visible_dimensions(tmp_path):
    src=tmp_path/'rotated.pdf'; c=canvas.Canvas(str(src),pagesize=(300,500)); c.drawString(10,10,'portrait'); c.save()
    reader=PdfReader(src); p=reader.pages[0]; p.rotate(90); w=PdfWriter(); w.add_page(p)
    with src.open('wb') as f:w.write(f)
    dest=tmp_path/'out.pdf'; render(src,'pdf',dest,0,[],{})
    page=PdfReader(dest).pages[0]
    assert round(float(page.mediabox.width))==500 and round(float(page.mediabox.height))==300

def test_bold_italic_underline_are_applied(tmp_path):
    src=tmp_path/'bg.png'; Image.new('RGB',(500,300),'white').save(src); dest=tmp_path/'styled.pdf'
    styled=field(bold=True,italic=True,underline=True)
    assert render(src,'image',dest,0,[styled],{'participant_name':'Styled participant'})==[]
    assert 'Styled participant' in PdfReader(dest).pages[0].extract_text()

def test_large_font_size_is_preserved_when_text_fits(tmp_path):
    src=tmp_path/'bg.png'; Image.new('RGB',(1200,900),'white').save(src); dest=tmp_path/'large.pdf'
    large=field(font_size=320,min_font_size=300,max_width=1)
    assert render(src,'image',dest,0,[large],{'participant_name':'A'})==[]
    content=PdfReader(dest).pages[0].get_contents().get_data()
    assert b'320 Tf' in content
