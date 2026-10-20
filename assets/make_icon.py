"""Generate Transcribr's five-bar waveform and white/grey gradient rim."""
from pathlib import Path
import math
from PIL import Image, ImageDraw, ImageFilter

root = Path(__file__).resolve().parent
size = 1024
outer = Image.new('L', (size,size)); ImageDraw.Draw(outer).rounded_rectangle((20,20,1003,1003), radius=215, fill=255)
inner = Image.new('L', (size,size)); ImageDraw.Draw(inner).rounded_rectangle((52,52,971,971), radius=185, fill=255)
from PIL import ImageChops
rim = ImageChops.subtract(outer,inner)
gradient = Image.new('RGBA',(size,size))
pixels = gradient.load()
for y in range(size):
    for x in range(size):
        t=(x+y)/(2*(size-1))
        shade=round(242-110*t+12*math.cos(t*math.pi*2))
        pixels[x,y]=(shade,shade,shade,255)
image = Image.new('RGBA',(size,size),(0,0,0,0))
image.paste((6,6,7,255),(0,0,size,size),outer)
glow = rim.filter(ImageFilter.GaussianBlur(15)).point(lambda a:round(a*.24))
image.paste(gradient,(0,0),ImageChops.multiply(glow,outer))
image.paste(gradient,(0,0),rim)
bars = Image.new('L',(size,size)); draw=ImageDraw.Draw(bars)
for center,height in zip((272,392,512,632,752),(184,328,480,328,184)):
    draw.rounded_rectangle((center-32,512-height//2,center+32,512+height//2),radius=32,fill=255)
wave = Image.new('RGBA',(size,size)); draw=ImageDraw.Draw(wave)
for y in range(size):
    value=round(255-43*y/(size-1));draw.line((0,y,size,y),fill=(value,value,value,255))
image.paste(wave,(0,0),bars)
image.save(root/'transcribr.ico',sizes=[(16,16),(20,20),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
image.resize((512,512),Image.Resampling.LANCZOS).save(root/'transcribr.png')
