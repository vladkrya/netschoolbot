# Заставки для TikTok: рисуют 2,5–4 с перед роликом. Берут фон, телефон и
# кадры записи из compose.py (exec его начала), склеиваются с готовым роликом.
import json, math, subprocess, sys, bisect
import os
HERE = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
REC = os.environ.get('REC', 'out/rec')
TIMER_REC = os.environ.get('TIMER_REC', 'out/rec_timer')
sys.argv = ['compose.py', REC, '/dev/null']
src = open(os.path.join(HERE, 'compose.py')).read()
exec(src[:src.index('import os\n# Сайт')])
from PIL import Image, ImageDraw, ImageFilter, ImageFont

big = ImageFont.truetype(FONT + 'Inter-ExtraBold.otf', 84)
mid = ImageFont.truetype(FONT + 'Inter-SemiBold.otf', 44)
DARK = (11, 14, 22)

def ease(x):
    x = max(0, min(1, x))
    return x * x * (3 - 2 * x)

def phone(t_rec, finger=True):
    img = BG.copy()
    img.paste(app_frame(t_rec), (SPX, SPY), MASK_S)
    img = img.convert('RGBA')
    if finger:
        f = finger_at(t_rec)
        if f:
            over = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(over)
            x, y, press, fade = f
            r = (40 if press else 46) * S
            cx, cy = SPX + x * 2 * AW / 720 * S, SPY + (SB + y * 2 * AH / 1520) * S
            d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, int(90 * fade)), outline=(255, 255, 255, int(200 * fade)), width=4)
            img = Image.alpha_composite(img, over)
    return img

def caption(img, title, sub, a=1.0, y=92):
    over = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(over)
    shift = int((1 - a) * 14)
    text_center(d, y - shift, title, title_font, (255, 255, 255, int(255 * a)))
    if sub: text_center(d, y + 90 - shift, sub, sub_font, (185, 195, 221, int(255 * a)))
    return Image.alpha_composite(img, over)

def curtain(img, lift, lines, a=1.0, spinner=None):
    """Тёмная шторка с крупным текстом; lift 0..1 — уезжает вверх."""
    off = -int(H * ease(lift))
    over = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d = ImageDraw.Draw(over)
    d.rectangle((0, off, W, H + off), fill=DARK + (255,))
    y0 = 800 - len(lines) * 55 + off
    for i, (text, font, col) in enumerate(lines):
        text_center(d, y0 + i * 112, text, font, col + (int(255 * a),))
    if spinner is not None:
        cx, cy, r = W // 2, 1150 + off, 44
        d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(60, 66, 82, 255), width=10)
        s = spinner * 360
        d.arc((cx - r, cy - r, cx + r, cy + r), s, s + 100, fill=(140, 150, 175, 255), width=10)
    # край шторки — мягкая тень
    if lift > 0:
        for k in range(30):
            d.line((0, H + off + k, W, H + off + k), fill=(0, 0, 0, int(90 * (1 - k / 30))))
    return Image.alpha_composite(img, over)

def encoder(path):
    return subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS), '-i', '-',
        '-vf', 'scale=out_color_matrix=bt709:out_range=full,format=yuv420p',
        '-color_range', 'pc', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
        '-c:v', 'libx264', '-preset', 'slow', '-b:v', '20000k', '-minrate', '20000k', '-maxrate', '20000k', '-bufsize', '20000k',
        '-x264-params', 'nal-hrd=cbr:force-cfr=1', '-movflags', '+faststart', path], stdin=subprocess.PIPE)

def render(name, dur, draw):
    import os
    p = encoder(os.environ.get('PREFIX', 'intro_') + f'{name}.mp4')
    for n in range(int(dur * FPS)):
        p.stdin.write(draw(n / FPS).convert('RGB').tobytes())
    p.stdin.close(); p.wait()

WHITE, SOFT = (255, 255, 255), (185, 195, 221)

# 1. Боль → решение
def pain(t):
    img = phone(0, finger=False)
    if t < 2.05:
        lift = (t - 1.45) / 0.5
        img = curtain(img, lift, [('«Сетевой город»', big, WHITE), ('опять не грузится?', big, WHITE)], spinner=t * 1.6)
    if t > 1.75:
        a = min(1, (t - 1.75) / 0.3) * min(1, (2.9 - t) / 0.25)
        img = caption(img, 'Есть «Дневник»', 'всё открывается сразу', max(0, a))
    return img

# 2. Вопрос → «Если получить ещё»
WI = 31.75
def question(t):
    img = phone(WI + max(0, t - 1.3))
    if t < 1.9:
        img = curtain(img, (t - 1.3) / 0.5, [('Сколько пятёрок', big, WHITE), ('нужно до «5»', big, WHITE), ('в четверти?', big, WHITE)])
    if t > 1.6:
        a = min(1, (t - 1.6) / 0.3) * min(1, (4.2 - t) / 0.25)
        img = caption(img, 'Дневник посчитает', 'тапни оценку — балл пересчитается', max(0, a))
    return img

# 3. Быстрая нарезка
CUTS = [5.2, 22.75, 32.0, 45.3, 48.7, 40.4]
def montage(t):
    i = min(len(CUTS) - 1, int(t / 0.42))
    img = phone(CUTS[i] + (t - i * 0.42))
    a = min(1, t / 0.2) * min(1, (len(CUTS) * 0.42 - t) / 0.2)
    return caption(img, 'Дневник, который', 'не стыдно открыть', max(0, a))

# 4. Таймер крупно
td = json.load(open(os.path.join(TIMER_REC, 'timeline.json')))
tframes = [(ts - td['start'], n) for n, ts in td['frames']]
ttimes = [x for x, _ in tframes]
tcache = {}
def tframe(n):
    if n not in tcache: tcache[n] = Image.open(os.path.join(TIMER_REC, n)).convert('RGB')
    return tcache[n]
def timer_at(x):
    i = max(0, bisect.bisect_right(ttimes, x) - 1)
    j = min(len(tframes) - 1, i + 1)
    a, b = tframe(tframes[i][1]), tframe(tframes[j][1])
    k = 0 if j == i else max(0, min(1, (x - ttimes[i]) / (ttimes[j] - ttimes[i])))
    return Image.blend(a, b, k) if k > 0.05 else a
BLUR = Image.alpha_composite(BG.filter(ImageFilter.GaussianBlur(30)).convert('RGBA'), Image.new('RGBA', (W, H), DARK + (150,)))
def timer(t):
    img = BLUR.copy()
    card = timer_at(0.1 + t)
    cw = 1000; card = card.resize((cw, int(card.height * cw / card.width)), Image.LANCZOS)
    m = Image.new('L', card.size, 0); ImageDraw.Draw(m).rounded_rectangle((8, 8, card.width - 8, card.height - 8), 48, fill=255)
    s = 0.92 + 0.08 * ease(t / 0.6)
    c2 = card.resize((int(card.width * s), int(card.height * s))); m2 = m.resize(c2.size)
    img.paste(c2, ((W - c2.width) // 2, 900 - c2.height // 2), m2)
    d = ImageDraw.Draw(img)
    a = min(1, t / 0.3)
    text_center(d, 470, 'Знаешь, сколько', big, WHITE + (int(255 * a),))
    text_center(d, 582, 'до конца урока?', big, WHITE + (int(255 * a),))
    if t > 3.35:
        b = min(1, (t - 3.35) / 0.3)
        text_center(d, 1180, 'До секунды.', big, (125, 220, 140, int(255 * b)))
        text_center(d, 1300, 'Дневник для «Сетевого города»', mid, SOFT + (int(255 * b),))
    return img

for name, dur, fn in [('pain', 2.9, pain), ('question', 4.2, question), ('montage', len(CUTS) * 0.42, montage), ('timer', 4.3, timer)]:
    render(name, dur, fn); print('ok', name, dur, flush=True)
