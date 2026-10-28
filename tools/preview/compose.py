# Собирает превью 1080×1920: фон, телефон с записью приложения, подписи сцен,
# «палец» и финальная заставка. Кадры скринкаста приходят неравномерно —
# на каждый выходной кадр берётся последний пришедший к этому моменту.
import bisect, json, math, os, subprocess, sys
from PIL import Image, ImageDraw, ImageFilter, ImageFont

rec, out = sys.argv[1], sys.argv[2]
W, H, FPS = 1080, 1920, 30
data = json.load(open(f'{rec}/timeline.json'))
events = data['events']
t0 = next(e['t'] for e in events if e['kind'] == 'start')
frames = [(ts - t0, name) for name, ts in data['frames']]
times = [t for t, _ in frames]
end_t = next(e['t'] for e in events if e['kind'] == 'end') - t0
duration = end_t + float(os.environ.get('TAIL', '2.8'))

FONT = os.environ.get('FONT_DIR', '/usr/share/fonts/opentype/inter').rstrip('/') + '/'
title_font = ImageFont.truetype(FONT + 'Inter-ExtraBold.otf', 64)
sub_font = ImageFont.truetype(FONT + 'Inter-Medium.otf', 34)
end_title = ImageFont.truetype(FONT + 'Inter-ExtraBold.otf', 96)
import os
_E = float(os.environ.get('END_SCALE', '1'))
end_sub = ImageFont.truetype(FONT + 'Inter-Medium.otf', round(38 * _E))
end_url = ImageFont.truetype(FONT + 'Inter-SemiBold.otf', round(44 * _E))

# Телефон в духе iPhone: экран = строка состояния + запись приложения
# (720×1520, уменьшается до AW×AH), по центру, под подписью.
AW, AH = 680, 1436
SB = 96                      # строка состояния
PW, PH = AW, AH + SB
PX, PY = (W - PW) // 2, 290
R = 108
# Раскладка для TikTok: телефон меньше и ниже, подписи — под интерфейсом
# TikTok (сверху у него строка поиска). По умолчанию — как для Telegram.
S = float(os.environ.get('PHONE_SCALE', '1'))
SPW, SPH = round(PW * S), round(PH * S)
SPX = (W - SPW) // 2
SPY = int(os.environ.get('PHONE_Y', PY))
SR = round(R * S)
CAP_Y = os.environ.get('CAP_Y')
WM_Y = int(os.environ.get('WM_Y', 222))

def background():
    bg = Image.new('RGB', (W, H), (11, 14, 22))
    grad = Image.new('RGB', (W, H))
    px = grad.load()
    for y in range(H):
        for x in range(0, W, 4):
            d = math.hypot((x - 220) / W, (y - 0) / H * 0.9)
            k = max(0.0, 1 - d * 1.25)
            c = (int(11 + (42 - 11) * k), int(14 + (58 - 14) * k), int(22 + (110 - 22) * k))
            for i in range(4):
                if x + i < W: px[x + i, y] = c
    bg = grad
    glow = Image.new('RGB', (W, H), (0, 0, 0))
    g = ImageDraw.Draw(glow)
    g.ellipse((-260, 1050, 560, 1870), fill=(74, 111, 174))
    g.ellipse((700, 120, 1400, 820), fill=(34, 160, 75))
    glow = glow.filter(ImageFilter.GaussianBlur(160))
    bg = Image.blend(bg, Image.eval(Image.composite(glow, bg, Image.new('L', (W, H), 255)), lambda v: v), 0.0)
    bg = Image.fromarray if False else bg
    from PIL import ImageChops
    bg = ImageChops.add(bg, Image.eval(glow, lambda v: int(v * 0.35)))
    # Корпус телефона с тенью: титановая рамка, чёрный ободок, кнопки.
    shadow = Image.new('L', (W, H), 0)
    ImageDraw.Draw(shadow).rounded_rectangle((SPX - round(24 * S), SPY + round(14 * S), SPX + SPW + round(24 * S), SPY + SPH + round(64 * S)), SR + 24, fill=170)
    shadow = shadow.filter(ImageFilter.GaussianBlur(42))
    bg.paste((0, 0, 0), (0, 0), shadow)
    d = ImageDraw.Draw(bg)
    B = round(22 * S)  # чёрный ободок вокруг экрана
    # кнопки по бокам — под рамкой, торчат наружу
    side = (88, 92, 102)
    for y0, y1 in ((SPY + round(250 * S), SPY + round(310 * S)), (SPY + round(360 * S), SPY + round(470 * S)), (SPY + round(500 * S), SPY + round(610 * S))):
        d.rounded_rectangle((SPX - B - 9, y0, SPX - B + 4, y1), 4, fill=side)
    d.rounded_rectangle((SPX + SPW + B - 4, SPY + round(400 * S), SPX + SPW + B + 9, SPY + round(560 * S)), 4, fill=side)
    d.rounded_rectangle((SPX - B - 4, SPY - B - 4, SPX + SPW + B + 4, SPY + SPH + B + 4), SR + B + 4, fill=(120, 124, 134))
    d.rounded_rectangle((SPX - B - 1, SPY - B - 1, SPX + SPW + B + 1, SPY + SPH + B + 1), SR + B + 1, fill=(58, 61, 70))
    d.rounded_rectangle((SPX - B, SPY - B, SPX + SPW + B, SPY + SPH + B), SR + B, fill=(3, 4, 6))
    return bg

BG = background()
MASK = Image.new('L', (PW, PH), 0)
ImageDraw.Draw(MASK).rounded_rectangle((0, 0, PW - 1, PH - 1), R, fill=255)
MASK_S = MASK.resize((SPW, SPH), Image.LANCZOS)
SB_FONT = ImageFont.truetype(FONT + 'Inter-SemiBold.otf', 34)

def screen(app):
    """Экран: строка состояния цвета верха приложения + само приложение."""
    top = app.getpixel((AW // 2, 4))
    scr = Image.new('RGB', (PW, PH), top)
    scr.paste(app, (0, SB))
    d = ImageDraw.Draw(scr)
    light = sum(top) / 3 > 140
    ink = (0, 0, 0) if light else (255, 255, 255)
    d.text((88, 30), '9:41', font=SB_FONT, fill=ink)
    # Dynamic Island
    d.rounded_rectangle((PW // 2 - 126, 18, PW // 2 + 126, 18 + 74), 37, fill=(0, 0, 0))
    # связь: четыре столбика
    x = PW - 196
    for i, h in enumerate((10, 15, 20, 25)):
        d.rounded_rectangle((x + i * 9, 58 - h, x + i * 9 + 6, 58), 2, fill=ink)
    # Wi-Fi: три дуги
    cx, cy = PW - 136, 60
    for r in (8, 16, 24):
        d.arc((cx - r, cy - r, cx + r, cy + r), 225, 315, fill=ink, width=5)
    # батарея
    bx = PW - 104
    d.rounded_rectangle((bx, 36, bx + 50, 60), 7, outline=ink, width=3)
    d.rounded_rectangle((bx + 5, 41, bx + 40, 55), 3, fill=ink)
    d.rounded_rectangle((bx + 53, 43, bx + 57, 53), 2, fill=ink)
    return scr

captions = [(e['t'] - t0, e['title'], e['sub']) for e in events if e['kind'] == 'caption']
fingers = [(e['t'] - t0, e) for e in events if e['kind'] == 'finger']
finger_times = [t for t, _ in fingers]

def caption_at(t):
    idx = bisect.bisect_right([c[0] for c in captions], t) - 1
    if idx < 0: return None, 0
    start, title, sub = captions[idx]
    alpha = min(1, (t - start) / 0.35)
    nxt = captions[idx + 1][0] if idx + 1 < len(captions) else end_t
    alpha = min(alpha, max(0, (nxt - t) / 0.25))
    return (title, sub), alpha

def finger_at(t):
    idx = bisect.bisect_right(finger_times, t) - 1
    if idx < 0: return None
    e = fingers[idx][1]
    if not e['on']:
        fade = 1 - (t - fingers[idx][0]) / 0.2
        if fade <= 0: return None
        return e['x'], e['y'], False, fade
    return e['x'], e['y'], e['press'], 1

cache = {}
def app_frame(t):
    i = max(0, bisect.bisect_right(times, t) - 1)
    name = frames[i][1]
    if name not in cache:
        cache.clear()
        im = Image.open(f'{rec}/{name}').convert('RGB')
        if im.size != (AW, AH): im = im.resize((AW, AH), Image.LANCZOS)
        scr = screen(im)
        cache[name] = scr if S == 1 else scr.resize((SPW, SPH), Image.LANCZOS)
    return cache[name]

def text_center(d, y, text, font, fill):
    w = d.textlength(text, font=font)
    d.text(((W - w) / 2, y), text, font=font, fill=fill)

import os
# Сайт и Telegram строкой между подписью и телефоном — весь ролик.
WM = os.environ.get('WM') == '1'
wm_font = ImageFont.truetype(FONT + 'Inter-SemiBold.otf', 27)
def draw_wm(d, a=1.0):
    text_center(d, WM_Y, 'sgoshka.ru   ·   Telegram @netschoolbot', wm_font, (150, 172, 222, int(235 * a)))

STILLS = [float(x) for x in os.environ.get('STILLS', '').split(',') if x]
proc = None if STILLS else subprocess.Popen(['ffmpeg', '-y', '-loglevel', 'error', '-f', 'rawvideo', '-pix_fmt', 'rgb24', '-s', f'{W}x{H}', '-r', str(FPS),
                         '-i', '-',
                         # RGB → YUV по матрице BT.709, полный диапазон 0–255.
                         '-vf', 'scale=out_color_matrix=bt709:out_range=full,format=yuv420p',
                         '-color_range', 'pc', '-colorspace', 'bt709', '-color_primaries', 'bt709', '-color_trc', 'bt709',
                         # CBR 20 000 кбит/с: битрейт закреплён снизу и сверху, HRD в режиме CBR.
                         '-c:v', 'libx264', '-preset', 'slow',
                         '-b:v', '20000k', '-minrate', '20000k', '-maxrate', '20000k', '-bufsize', '20000k',
                         '-x264-params', 'nal-hrd=cbr:force-cfr=1',
                         '-movflags', '+faststart', out],
                        stdin=subprocess.PIPE)
total = int(duration * FPS)
for n in ([int(x * FPS) for x in STILLS] or range(total)):
    t = n / FPS
    img = BG.copy()
    img.paste(app_frame(min(t, times[-1] + 10)), (SPX, SPY), MASK_S)
    over = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(over)
    cap, a = caption_at(t)
    if cap and a > 0:
        shift = int((1 - a) * 14)
        ty = int(CAP_Y) if CAP_Y else (74 if WM else 92)
        text_center(d, ty - shift, cap[0], title_font, (255, 255, 255, int(255 * a)))
        text_center(d, ty + 90 - shift, cap[1], sub_font, (185, 195, 221, int(255 * a)))
    if WM and t <= end_t:
        draw_wm(d)
    f = finger_at(t)
    if f:
        x, y, press, fade = f
        r = (40 if press else 46) * S
        cx, cy = SPX + x * 2 * AW / 720 * S, SPY + (SB + y * 2 * AH / 1520) * S
        d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(255, 255, 255, int(90 * fade)), outline=(255, 255, 255, int(200 * fade)), width=4)
    if t > end_t:
        k = min(1, (t - end_t) / 0.6)
        d.rectangle((0, 0, W, H), fill=(11, 14, 22, int(225 * k)))
        text_center(d, 700, 'Дневник', end_title, (255, 255, 255, int(255 * k)))
        text_center(d, 840, 'приложение для «Сетевого города»', end_sub, (185, 195, 221, int(255 * k)))
        text_center(d, 960, 'sgoshka.ru', end_url, (255, 255, 255, int(255 * k)))
        text_center(d, 1040, 'Telegram: @netschoolbot', end_url, (125, 170, 255, int(255 * k)))
    img = Image.alpha_composite(img.convert('RGBA'), over).convert('RGB')
    if STILLS:
        img.save(f'{out}_{t:.2f}.png')
        continue
    proc.stdin.write(img.tobytes())
if proc:
    proc.stdin.close()
    proc.wait()
print('frames', total, 'seconds', round(duration, 1))
