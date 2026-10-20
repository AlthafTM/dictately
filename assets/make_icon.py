"""Generate Transcribr's five-bar waveform and white/grey gradient rim."""
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFilter

root = Path(__file__).resolve().parent
size = 1024
outer = Image.new('L', (size,size)); ImageDraw.Draw(outer).rounded_rectangle((20,20,1003,1003), radius=215, fill=255)
inner = Image.new('L', (size,size)); ImageDraw.Draw(inner).rounded_rectangle((44,44,979,979), radius=191, fill=255)
from PIL import ImageChops
rim = ImageChops.subtract(outer,inner)
gradient = Image.new('RGBA',(size,size))
pixels = gradient.load()
for y in range(size):
    for x in range(size):
        t=(x+y)/(2*(size-1))
        # Broad reflected highlights fade smoothly along the perimeter.
        shade=round(182+67*math.cos((t-.12)*math.pi)+6*math.sin((x-y)/size*math.pi))
        pixels[x,y]=(shade,shade,shade,255)
image = Image.new('RGBA',(size,size),(0,0,0,0))
image.paste((6,6,7,255),(0,0,size,size),outer)
# Two falloffs make the rim spill into the face without a hard inner stripe.
# Clip to the silhouette so Windows keeps clean transparent corners.
wide_glow = rim.filter(ImageFilter.GaussianBlur(60)).point(lambda a:min(255,round(a*1.7)))
near_glow = rim.filter(ImageFilter.GaussianBlur(18)).point(lambda a:round(a*.8))
glow = ImageChops.add(wide_glow,near_glow)
image.paste(gradient,(0,0),ImageChops.multiply(glow,outer))
image.paste(gradient,(0,0),rim)
bars = Image.new('L',(size,size)); draw=ImageDraw.Draw(bars)
for center,height in zip((272,392,512,632,752),(184,328,480,328,184)):
    draw.rounded_rectangle((center-32,512-height//2,center+32,512+height//2),radius=32,fill=255)
wave = Image.new('RGBA',(size,size)); draw=ImageDraw.Draw(wave)
for y in range(size):
    value=round(255-43*y/(size-1));draw.line((0,y,size,y),fill=(value,value,value,255))
image.paste(wave,(0,0),bars)
image.putalpha(outer)
image.save(root/'transcribr.ico',sizes=[(16,16),(20,20),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
image.resize((512,512),Image.Resampling.LANCZOS).save(root/'transcribr.png')
