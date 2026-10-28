import os
__file__ = os.path.abspath(__file__)
src = open(os.path.join(os.path.dirname(__file__), 'intros.py')).read()
exec(src[:src.index('# 1. Боль')])
wm_font = ImageFont.truetype(FONT + 'Inter-SemiBold.otf', 27)
url_font = ImageFont.truetype(FONT + 'Inter-SemiBold.otf', 40)
RED = (255, 107, 107)
import os
URL_Y = int(os.environ.get('URL_Y', 1500))
PREFIX = os.environ.get('PREFIX', 'intro_')

def layer():
    return Image.new('RGBA', (W, H), (0, 0, 0, 0))

# Запись идёт под шторкой: к концу заставки (3,4 с) она дошла до REC_END,
# и основной ролик продолжается с того же места — таймер не замирает.
REC_END = 1.7
def pain2(t, label='«Сетевой город»'):
    img = phone(max(0, t - (3.4 - REC_END)), finger=False)
    # под шторкой: строка с сайтом и подпись — их открывает уезжающая шторка
    over = layer(); d = ImageDraw.Draw(over)
    text_center(d, WM_Y, 'sgoshka.ru   ·   Telegram @netschoolbot', wm_font, (150, 172, 222, 235))
    img = Image.alpha_composite(img, over)
    if t > 2.15:
        a = min(1, (t - 2.15) / 0.3) * min(1, (3.4 - t) / 0.25)
        img = caption(img, 'Есть «Дневник»', 'всё открывается сразу', max(0, a), y=int(CAP_Y) if CAP_Y else 74)
    if t >= 2.45:
        return img
    lift = (t - 1.95) / 0.5
    off = -int(H * ease(lift))
    cur = layer(); d = ImageDraw.Draw(cur)
    d.rectangle((0, off, W, H + off), fill=DARK + (255,))
    if lift > 0:
        for k in range(30):
            d.line((0, H + off + k, W, H + off + k), fill=(0, 0, 0, int(90 * (1 - k / 30))))
    img = Image.alpha_composite(img, cur)
    txt = layer(); d = ImageDraw.Draw(txt)
    a1 = min(1, t / 0.25)
    sh1 = int((1 - a1) * 20)
    # «не грузится?» чуть позже и с лёгкой дрожью — как зависшая страница
    a2 = min(1, max(0, (t - 0.45) / 0.2))
    jx = int(6 * math.sin(t * 40)) if 0.45 < t < 0.9 else 0
    text_center(d, 690 + off + sh1, label, big, WHITE + (int(255 * a1),))
    if a2 > 0:
        txt2 = Image.new('RGBA', (W, H), (0, 0, 0, 0)); d2 = ImageDraw.Draw(txt2)
        text_center(d2, 800 + off, 'не грузится?', big, RED + (int(255 * a2),))
        txt = Image.alpha_composite(txt, txt2.transform(txt2.size, Image.AFFINE, (1, 0, -jx, 0, 1, 0)))
        d = ImageDraw.Draw(txt)
    cx, cy, r = W // 2, 1150 + off, 44
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=(60, 66, 82, 255), width=10)
    s = t * 1.6 * 360
    d.arc((cx - r, cy - r, cx + r, cy + r), s, s + 100, fill=(140, 150, 175, 255), width=10)
    text_center(d, URL_Y + off, 'sgoshka.ru', url_font, WHITE + (200,))
    text_center(d, URL_Y + 60 + off, 'Telegram @netschoolbot', url_font, (125, 170, 255, 220))
    return Image.alpha_composite(img, txt)

render('sgo', 3.4, lambda t: pain2(t, '«Сетевой город»'))
render('netschool', 3.4, lambda t: pain2(t, 'NetSchool'))
print('ok')
