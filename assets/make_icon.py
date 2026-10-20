"""Generate Dictately's left-peaking waveform with soft reflected light."""
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFilter

root = Path(__file__).resolve().parent
size = 1024
outer = Image.new('L', (size,size)); ImageDraw.Draw(outer).rounded_rectangle((20,20,1003,1003), radius=215, fill=255)
inner = Image.new('L', (size,size)); ImageDraw.Draw(inner).rounded_rectangle((34,34,989,989), radius=201, fill=255)
from PIL import ImageChops
rim = ImageChops.subtract(outer,inner)
gradient = Image.new('RGBA',(size,size))
pixels = gradient.load()
for y in range(size):
    for x in range(size):
        # Broad reflections vary smoothly around the rim.
        key=math.exp(-(((x-180)/640)**2+((y-110)/620)**2))
        bounce=math.exp(-(((x-920)/420)**2+((y-880)/520)**2))
        shade=round(min(255,83+169*key+121*bounce))
        pixels[x,y]=(shade,shade,shade,255)
image = Image.new('RGBA',(size,size),(0,0,0,0))
image.paste((6,6,7,255),(0,0,size,size),outer)
# Two falloffs make the rim spill into the face without a hard inner stripe.
# Clip to the silhouette so Windows keeps clean transparent corners.
wide_glow = rim.filter(ImageFilter.GaussianBlur(58)).point(lambda a:min(255,round(a*2.3)))
near_glow = rim.filter(ImageFilter.GaussianBlur(15)).point(lambda a:min(255,round(a*1.15)))
glow = ImageChops.add(wide_glow,near_glow)
image.paste(gradient,(0,0),ImageChops.multiply(glow,outer))
image.paste(gradient,(0,0),rim)
bars = Image.new('L',(size,size)); draw=ImageDraw.Draw(bars)
for center,height in zip((274,393,512,631,750),(244,550,446,334,170)):
    draw.rounded_rectangle((center-35,512-height//2,center+35,512+height//2),radius=35,fill=255)
# Subtle glow behind the sharp waveform keeps small icons legible.
halo=bars.filter(ImageFilter.GaussianBlur(22)).point(lambda a:round(a*.30))
image.paste((218,222,228,255),(0,0,size,size),ImageChops.multiply(halo,outer))
wave = Image.new('RGBA',(size,size)); draw=ImageDraw.Draw(wave)
for y in range(size):
    t=y/(size-1)
    value=round(255-64*(t*t*(3-2*t)))
    draw.line((0,y,size,y),fill=(value,value,min(255,value+3),255))
image.paste(wave,(0,0),bars)
image.putalpha(outer)
image.save(root/'dictately.ico',sizes=[(16,16),(20,20),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
image.resize((512,512),Image.Resampling.LANCZOS).save(root/'dictately.png')
