# Бодрый поп-хаус, синтезирован с нуля. До REVEAL — приглушённые аккорды и
# нарастающий шум, в REVEAL — удар тарелки и вступает бит.
import sys, wave, numpy as np
from scipy.signal import lfilter, butter, fftconvolve
SR = 44100
LENGTH = float(sys.argv[1]); REVEAL = float(sys.argv[2]); OUT = sys.argv[3]
rng = np.random.default_rng(11)
N = int(LENGTH * SR); tt = np.arange(N) / SR
BPM = 120; BEAT = 60 / BPM; BAR = BEAT * 4
def hz(m): return 440 * 2 ** ((m - 69) / 12)
# C  G  Am  F — аккорды (средний регистр) и корни баса
CH = [[60, 64, 67, 72], [59, 62, 67, 71], [57, 60, 64, 69], [57, 60, 65, 69]]
ROOT = [36, 43, 45, 41]

def add(buf, start, sig, g=1.0):
    i = int(round(start * SR))
    if i >= len(buf) or i + len(sig) <= 0: return
    if i < 0: sig = sig[-i:]; i = 0
    n = min(len(sig), len(buf) - i); buf[i:i + n] += sig[:n] * g

def env_t(dur): return np.arange(int(dur * SR)) / SR

def saw(f, dur, bright=10):
    t = env_t(dur)
    return sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, bright + 1)) * 0.6

def stab(f, dur=0.22):
    t = env_t(dur)
    s = saw(f, dur, 8) + saw(f * 1.006, dur, 8)
    return s * np.minimum(1, t / 0.004) * np.exp(-t * 11)

def pluck(f, dur=0.25):
    t = env_t(dur)
    s = np.sin(2 * np.pi * f * t) + 0.5 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 20) + 0.25 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 30)
    return s * np.minimum(1, t / 0.002) * np.exp(-t * 14)

def bassnote(f, dur):
    t = env_t(dur)
    s = np.sin(2 * np.pi * f * t) + 0.35 * np.sin(2 * np.pi * 2 * f * t) + 0.12 * np.sin(2 * np.pi * 3 * f * t)
    return s * np.minimum(1, t / 0.005) * np.minimum(1, (dur - t) / 0.02) * np.exp(-t * 3)

def kick():
    t = env_t(0.3); f = 48 + 110 * np.exp(-t * 35)
    return np.tanh(1.6 * np.sin(2 * np.pi * np.cumsum(f) / SR)) * np.exp(-t * 10)

def clap():
    t = env_t(0.22); b, a = butter(2, [900 / (SR / 2), 5000 / (SR / 2)], 'band')
    n = lfilter(b, a, rng.normal(0, 1, len(t)))
    e = np.exp(-t * 22) + 0.6 * np.exp(-np.maximum(0, t - 0.012) * 30) * (t > 0.012)
    return n * e

def hat(open_=False):
    d = 0.18 if open_ else 0.045; t = env_t(d)
    b, a = butter(2, 8000 / (SR / 2), 'high')
    return lfilter(b, a, rng.normal(0, 1, len(t))) * np.exp(-t * (16 if open_ else 80))

def crash():
    t = env_t(2.2); b, a = butter(2, 4000 / (SR / 2), 'high')
    return lfilter(b, a, rng.normal(0, 1, len(t))) * np.exp(-t * 2.2)

chords = np.zeros(N); lead = np.zeros(N); bass = np.zeros(N); drums = np.zeros(N); fx = np.zeros(N)
bar = 0; start = REVEAL - 2 * BAR
while start < LENGTH:
    c = CH[bar % 4]; r = ROOT[bar % 4]
    main = start >= REVEAL - 1e-6
    for b in range(4):
        bt = start + b * BEAT
        # аккорды на слабые доли (и на сильные во вступлении)
        for m in c:
            add(chords, bt + BEAT / 2, stab(hz(m)), 0.11)
            if not main: add(chords, bt, stab(hz(m)), 0.05)
        if main:
            last = (bar % 8 == 7 and b == 3)
            if not last: add(drums, bt, kick(), 0.48)
            if b in (1, 3): add(drums, bt, clap(), 0.30)
            add(drums, bt + BEAT / 2, hat(True), 0.10)
            for k in range(4): add(drums, bt + k * BEAT / 4, hat(), 0.05 if k % 2 else 0.035)
            if last:
                for k in range(4): add(drums, bt + k * BEAT / 4, clap(), 0.12 + 0.05 * k)
            # бас восьмыми: корень, октава вверх на слабую
            add(bass, bt, bassnote(hz(r), BEAT / 2 * 0.9), 0.24)
            add(bass, bt + BEAT / 2, bassnote(hz(r + 12), BEAT / 2 * 0.9), 0.22)
            # перебор шестнадцатыми из звуков аккорда, через такт — гуще
            pat = [0, 2, 3, 1] if bar % 2 == 0 else [3, 1, 2, 0]
            for k in range(4):
                if bar % 4 < 2 and k % 2: continue
                add(lead, bt + k * BEAT / 4, pluck(hz(c[pat[k]] + 12)), 0.26)
    start += BAR; bar += 1

# нарастающий шум перед открытием и тарелка в момент открытия
rs = int((REVEAL - 1.0) * SR); re = int(REVEAL * SR)
if rs < re:
    b, a = butter(2, 3000 / (SR / 2), 'high')
    noise = lfilter(b, a, rng.normal(0, 1, re - rs)) * np.linspace(0, 1, re - rs) ** 2
    fx[rs:re] += noise * 0.12
add(fx, REVEAL, crash(), 0.16)
add(drums, REVEAL, kick(), 0.4)

# до открытия аккорды «за стенкой», затем фильтр раскрывается
open_k = np.clip((tt - (REVEAL - 0.15)) / 0.25, 0, 1)
b1, a1 = butter(2, 900 / (SR / 2)); chords = lfilter(b1, a1, chords) * (1 - open_k) + chords * open_k
b2, a2 = butter(2, 5500 / (SR / 2)); chords = lfilter(b2, a2, chords)

# «пампинг» от бочки — аккорды и перебор приседают на каждую долю
ph = ((tt - REVEAL) % BEAT) / BEAT
duck = np.where(tt >= REVEAL, 0.45 + 0.55 * np.minimum(1, ph / 0.45), 1)
ir_t = np.arange(int(1.2 * SR)) / SR
ir = rng.normal(0, 1, len(ir_t)) * np.exp(-ir_t * 4.5); ir /= np.sqrt(np.sum(ir ** 2))
wet = fftconvolve(chords + lead, ir)[:N] * 0.18
mix = (chords + lead + wet) * duck + bass + drums + fx
fade_in = np.clip(tt / 0.2, 0, 1); fade_out = np.clip((LENGTH - tt) / 2.5, 0, 1)
mix *= fade_in * fade_out
rms = np.sqrt(np.mean(mix[int(REVEAL * SR):int((LENGTH - 3) * SR)] ** 2))
mix *= 10 ** (-15 / 20) / rms
mix = np.tanh(mix * 1.3) / np.tanh(1.3)
L = mix + 0.06 * np.roll(lead, 140) * duck; R = mix + 0.06 * np.roll(chords, 200) * duck
st = np.stack([L, R], 1); st *= 0.89 / np.max(np.abs(st))
with wave.open(OUT, 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR); w.writeframes((st * 32767).astype('<i2').tobytes())
print('ok', OUT)
