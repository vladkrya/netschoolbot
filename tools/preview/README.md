# Видео-превью приложения

Ролики собираются программно: Playwright проходит по демо-режиму
мини-приложения (`/mini/demo`) по сценарию, кадры обрабатываются на Python
и кодируются через ffmpeg. Готовые ролики лежат в `media/preview/` (Git LFS).

При клонировании и деплое они не скачиваются (`.lfsconfig`), чтобы не тянуть
~700 МБ на сервер. Скачать их локально:

```bash
git lfs pull --include="media/preview/*"
```

## Что получается

| Файл | Для чего |
|---|---|
| `telegram_preview.mp4` | превью мини-приложения в Telegram, не длиннее 60 с |
| `tiktok_{sgo,netschool}_{calm,lively}.mp4` | TikTok: заставка «"Сетевой город" / NetSchool не грузится?», спокойная или бодрая музыка |

Кодирование: H.264 High, 1080×1920, 30 к/с, BT.709, полный диапазон,
CBR 20 000 кбит/с; звук AAC 192 кбит/с.

## Как пересобрать

```bash
OUT=/tmp/preview tools/preview/build.sh
```

Нужно: Python с зависимостями проекта и `requirements-dev.txt`, а также
`pillow numpy scipy`; Node.js с Playwright и Chromium; ffmpeg; шрифт Inter.

| Переменная | Зачем |
|---|---|
| `PLAYWRIGHT_MODULE` | путь к Playwright, если он не ставится как `playwright` (например `/opt/node-tools/node_modules/playwright/index.mjs`) |
| `FONT_DIR` | папка со шрифтом Inter (по умолчанию `/usr/share/fonts/opentype/inter`) |
| `OUT` | куда класть записи и ролики (по умолчанию `tools/preview/out`) |

## Из чего состоит

| Файл | Что делает |
|---|---|
| `record.mjs` | сценарий: касания, свайпы, ввод, подписи сцен; пишет кадры и `timeline.json` |
| `test_record.py` | поднимает сервер бота, как в тестах, и запускает сценарий |
| `compose.py` | фон, корпус iPhone, строка состояния, подписи, «палец», финальная заставка, кодирование |
| `intro_pain.py` | заставка для TikTok «… не грузится?» со шторкой |
| `intros.py` | другие варианты заставок (вопрос, нарезка, таймер крупно — нужен `record_timer.mjs`) |
| `music_calm.py`, `music_lively.py` | музыка, синтезированная с нуля — прав ни у кого нет |
| `contact_sheet.py` | контактный лист кадров записи — проверять глазами |
| `build.sh` | всё вместе |

Раскладку задают переменные окружения `compose.py`: `PHONE_SCALE`,
`PHONE_Y`, `CAP_Y`, `WM_Y`, `END_SCALE`, `WM` (строка с сайтом и Telegram).
Для TikTok подписи опущены ниже его строки поиска, а телефон уменьшен, чтобы
не уходить под кнопки справа и под описание.

После правок сценария — проверять кадры: `contact_sheet.py rec sheet.png 1,5,10,…`.
