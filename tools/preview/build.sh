#!/usr/bin/env bash
# Полная пересборка превью: запись приложения → ролик для Telegram (≤ 60 с)
# → четыре ролика для TikTok (две заставки × две мелодии). Всё — в $OUT.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${OUT:-$HERE/out}"
mkdir -p "$OUT"
# Русская локаль — иначе поля времени в браузере пишут «02:30 PM».
export LANG="${LANG_REC:-ru_RU.UTF-8}"

# Кодирование: BT.709, полный диапазон, CBR 20 000 кбит/с.
ENC=(-color_range pc -colorspace bt709 -color_primaries bt709 -color_trc bt709
     -c:v libx264 -preset slow -b:v 20000k -minrate 20000k -maxrate 20000k -bufsize 20000k
     -x264-params nal-hrd=cbr:force-cfr=1 -r 30 -movflags +faststart)

echo "▶ запись приложения"
(cd "$ROOT" && SCRIPT="$HERE/record.mjs" ARGS="$OUT/rec" python -m pytest -q -s tools/preview/test_record.py)

cd "$OUT"
echo "▶ ролик для Telegram"
TAIL=2.4 python3 "$HERE/compose.py" rec telegram_preview.mp4

echo "▶ основа для TikTok: телефон меньше и ниже, подписи под интерфейсом TikTok"
export WM=1 PHONE_SCALE=0.74 PHONE_Y=500 CAP_Y=262 WM_Y=404 END_SCALE=0.84 URL_Y=1300 PREFIX=tk_ REC=rec
TAIL=2.4 python3 "$HERE/compose.py" rec main_tk.mp4
python3 "$HERE/intro_pain.py"

# Заставка 3,4 с, под шторкой запись идёт с начала; основной ролик
# продолжается с 1,7 с — таймер не повторяется и не замирает.
CUT=1.7; INTRO=3.4; REVEAL=2.15
MAIN=$(ffprobe -v error -show_entries format=duration -of csv=p=0 main_tk.mp4)
LEN=$(python3 -c "print(round($INTRO + $MAIN - $CUT, 2))")
python3 "$HERE/music_calm.py" "$LEN" "$REVEAL" calm.wav
python3 "$HERE/music_lively.py" "$LEN" "$REVEAL" lively.wav

for v in sgo netschool; do
  for m in calm lively; do
    vol=-3dB; [ "$m" = lively ] && vol=-2.3dB
    echo "▶ tiktok_${v}_${m}.mp4"
    ffmpeg -y -loglevel error -i "tk_$v.mp4" -ss "$CUT" -i main_tk.mp4 -i "$m.wav" \
      -filter_complex "[0:v]setpts=PTS-STARTPTS[a];[1:v]setpts=PTS-STARTPTS[b];[a][b]concat=n=2:v=1,scale=out_color_matrix=bt709:out_range=full,format=yuv420p[v];[2:a]volume=$vol[au]" \
      -map "[v]" -map "[au]" "${ENC[@]}" -c:a aac -b:a 192k -ar 44100 -shortest "tiktok_${v}_${m}.mp4"
  done
done
ls -la "$OUT"/*.mp4
