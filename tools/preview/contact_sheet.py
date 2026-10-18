import json, sys, bisect
from PIL import Image, ImageDraw
rec, out = sys.argv[1], sys.argv[2]
ts = [float(x) for x in sys.argv[3].split(',')]
d = json.load(open(f'{rec}/timeline.json'))
t0 = next(e['t'] for e in d['events'] if e['kind'] == 'start')
fr = [(t - t0, n) for n, t in d['frames']]
times = [t for t, _ in fr]
cols = 6
w, h = 240, 507
rows = (len(ts) + cols - 1) // cols
sheet = Image.new('RGB', (cols * w, rows * (h + 24)), 'white')
dr = ImageDraw.Draw(sheet)
for i, t in enumerate(ts):
    k = max(0, bisect.bisect_right(times, t) - 1)
    im = Image.open(f'{rec}/{fr[k][1]}').resize((w, h))
    x, y = (i % cols) * w, (i // cols) * (h + 24)
    sheet.paste(im, (x, y + 24)); dr.text((x + 4, y + 4), f'{t:.1f}', fill='black')
sheet.save(out)
