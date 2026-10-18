# Спокойный lo-fi трек, синтезированный с нуля (без чужих сэмплов — прав
# ни у кого нет). Ударные вступают в момент REVEAL, к концу — затухание.
import sys, numpy as np
from scipy.signal import lfilter, butter, fftconvolve
import wave

SR = 44100
LENGTH = float(sys.argv[1]); REVEAL = float(sys.argv[2]); OUT = sys.argv[3]
rng = np.random.default_rng(7)
N = int(LENGTH * SR)
BPM = 92; BEAT = 60 / BPM; BAR = BEAT * 4
t0 = REVEAL - 1 * BAR          # сетка: такт начинается в момент открытия
def hz(m): return 440 * 2 ** ((m - 69) / 12)
CHORDS = [[53, 57, 60, 64], [52, 55, 59, 62], [50, 53, 57, 60], [48, 52, 55, 59]]  # Fmaj7 Em7 Dm7 Cmaj7

def add(buf, start, sig, gain=1.0):
    i = int(start * SR)
    if i >= len(buf) or i + len(sig) <= 0: return
    if i < 0: sig = sig[-i:]; i = 0
    n = min(len(sig), len(buf) - i)
    buf[i:i + n] += sig[:n] * gain

def ep(f, dur):
    """Электропиано: синус с обертонами и мягким затуханием, лёгкое тремоло."""
    t = np.arange(int(dur * SR)) / SR
    env = np.minimum(1, t / 0.012) * np.exp(-t * 1.3)
    s = np.sin(2 * np.pi * f * t) + 0.28 * np.sin(2 * np.pi * 2 * f * t) * np.exp(-t * 3) + 0.08 * np.sin(2 * np.pi * 3 * f * t) * np.exp(-t * 6)
    return s * env * (1 + 0.08 * np.sin(2 * np.pi * 4.5 * t))

def pluck(f, dur):
    """Карплус — Стронг: щипок струны."""
    n = int(dur * SR); p = max(2, int(SR / f))
    buf = rng.uniform(-1, 1, p); out = np.zeros(n)
    for i in range(n):
        out[i] = buf[i % p]
        buf[i % p] = 0.5 * (buf[i % p] + buf[(i + 1) % p]) * 0.996
    return out * np.exp(-np.arange(n) / SR * 2.5)

def padtone(f, dur):
    t = np.arange(int(dur * SR)) / SR
    env = np.minimum(1, t / 0.5) * np.minimum(1, (dur - t) / 0.5)
    return sum(np.sin(2 * np.pi * f * d * t) for d in (0.997, 1.0, 1.004)) * env / 3

def kick():
    t = np.arange(int(0.35 * SR)) / SR
    f = 52 + 70 * np.exp(-t * 28)
    return np.sin(2 * np.pi * np.cumsum(f) / SR) * np.exp(-t * 9)

def snare():
    t = np.arange(int(0.25 * SR)) / SR
    b, a = butter(2, [1200 / (SR / 2), 6000 / (SR / 2)], 'band')
    return (lfilter(b, a, rng.normal(0, 1, len(t))) * 0.8 + 0.3 * np.sin(2 * np.pi * 190 * t)) * np.exp(-t * 18)

def hat():
    t = np.arange(int(0.06 * SR)) / SR
    b, a = butter(2, 7000 / (SR / 2), 'high')
    return lfilter(b, a, rng.normal(0, 1, len(t))) * np.exp(-t * 70)

pad = np.zeros(N); keys = np.zeros(N); arp = np.zeros(N); bass = np.zeros(N); drums = np.zeros(N)
bar_i = 0
start = t0 - BAR * 2
while start < LENGTH:
    chord = CHORDS[bar_i % 4]
    if start + BAR > 0:
        for k, m in enumerate(chord):            # аккорд с лёгким «раскатом»
            add(keys, start + k * 0.018, ep(hz(m + 12), BAR * 1.15), 0.20)
            add(pad, start, padtone(hz(m), BAR * 1.05), 0.05)
        if start >= REVEAL - 0.01:
            add(bass, start, ep(hz(chord[0] - 12), BEAT * 2.2), 0.20)
            add(bass, start + BEAT * 2.5, ep(hz(chord[0] - 12), BEAT * 1.4), 0.15)
            pattern = [0, 2, 1, 3, 2, 1, 3, 2]
            for j, idx in enumerate(pattern):
                if j in (3, 7) and bar_i % 2: continue
                add(arp, start + j * BEAT / 2 + (0.03 if j % 2 else 0), pluck(hz(chord[idx] + 24), 0.9), 0.34)
            for b in range(4):
                bt = start + b * BEAT
                if b in (0, 2): add(drums, bt, kick(), 0.30)
                if b == 2 and bar_i % 2: add(drums, bt + BEAT / 2, kick(), 0.18)
                if b in (1, 3): add(drums, bt, snare(), 0.24)
                add(drums, bt, hat(), 0.10); add(drums, bt + BEAT / 2 + 0.035, hat(), 0.06)
    start += BAR; bar_i += 1

# До открытия клавиши приглушены (как через стенку), затем фильтр раскрывается.
tt = np.arange(N) / SR
open_k = np.clip((tt - (REVEAL - 0.35)) / 0.5, 0, 1)
b1, a1 = butter(2, 700 / (SR / 2)); muffled = lfilter(b1, a1, keys)
keys = muffled * (1 - open_k) + keys * open_k

ir_t = np.arange(int(1.6 * SR)) / SR
ir = rng.normal(0, 1, len(ir_t)) * np.exp(-ir_t * 3.2); ir /= np.sqrt(np.sum(ir ** 2))
wet = fftconvolve(keys + arp * 0.8, ir)[:N] * 0.22

mix = keys + pad + arp + bass + drums + wet
crackle = np.zeros(N); idx = rng.integers(0, N, int(LENGTH * 6)); crackle[idx] = rng.uniform(-1, 1, len(idx))
b2, a2 = butter(1, 3000 / (SR / 2), 'high'); mix += lfilter(b2, a2, crackle) * 0.05 + rng.normal(0, 0.003, N)
b3, a3 = butter(2, 9000 / (SR / 2)); mix = lfilter(b3, a3, mix)          # тёплый верх
fade_in = np.clip(tt / 0.4, 0, 1); fade_out = np.clip((LENGTH - tt) / 3.0, 0, 1)
mix *= fade_in * fade_out
rms = np.sqrt(np.mean(mix[int(REVEAL * SR):int((LENGTH - 3) * SR)] ** 2))
mix *= 10 ** (-16 / 20) / rms
mix = np.tanh(mix * 1.2) / np.tanh(1.2)
mix *= 10 ** (-1 / 20) / max(1e-9, np.max(np.abs(mix)))
# лёгкое стерео: клавиши и перебор чуть разведены
L = mix + 0.05 * np.roll(arp, 120); R = mix + 0.05 * np.roll(keys, 160)
st = np.stack([L, R], 1); st /= max(1, np.max(np.abs(st)) / 0.89)
with wave.open(OUT, 'wb') as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((st * 32767).astype('<i2').tobytes())
print('ok', OUT, 'rms dBFS', round(20 * np.log10(np.sqrt(np.mean(st ** 2))), 1), 'peak', round(float(np.max(np.abs(st))), 2))
