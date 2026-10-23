from pathlib import Path
from PIL import Image, ImageDraw

out=Path(__file__).resolve().parents[1]/'samples'/'sample-certificate.png'
out.parent.mkdir(parents=True,exist_ok=True)
im=Image.new('RGB',(1600,1100),'#fbfaf5'); d=ImageDraw.Draw(im)
d.rectangle((36,36,1564,1064),outline='#1d5248',width=8)
d.rectangle((56,56,1544,1044),outline='#c5a758',width=3)
d.text((800,260),'CERTIFICATE OF COMPLETION',fill='#1d5248',anchor='mm')
d.text((800,390),'This certificate is proudly presented to',fill='#59635e',anchor='mm')
d.line((360,665,1240,665),fill='#c5a758',width=3)
d.text((800,800),'Community Design Workshop · 2026',fill='#59635e',anchor='mm')
im.save(out)
print(out)
