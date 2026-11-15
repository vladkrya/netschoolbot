// Сканер QR-кода через камеру устройства для веб-приложения (PWA / браузер).
'use strict';

import { banner, esc } from './core.js';
import { closeSheet, openSheet } from './sheet.js';
import jsQR from './vendor/jsQR.js';

export function openWebQrScanner(onSuccess) {
  let stream = null;
  let scanTimer;
  let active = true;

  const html = `
    <div class="qr-scanner-modal">
      <div class="qr-viewfinder">
        <video id="qrVideo" playsinline autoplay muted></video>
        <div class="qr-target-box"></div>
      </div>
      <p class="muted small" style="text-align:center; margin-top:10px;">
        Наведите камеру на QR-код для входа
      </p>
      <input type="file" id="qrFileInput" accept="image/*" hidden>
      <div style="display:flex; justify-content:center; gap:10px; margin-top:12px;">
        <button class="btn outline" id="qrPickFile" type="button">Загрузить фото QR</button>
        <button class="btn" id="qrCancelBtn" type="button">Отмена</button>
      </div>
    </div>
  `;

  const stopCamera = () => {
    clearTimeout(scanTimer);
    stream?.getTracks().forEach((track) => track.stop());
    stream = null;
  };
  const sheet = openSheet('Сканирование QR-кода', html, { onClose: () => {
    active = false;
    stopCamera();
  } });

  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', { willReadFrequently: true });
  const decode = (source, width, height, maxSide) => {
    const scale = Math.min(1, maxSide / Math.max(width, height));
    const w = Math.max(1, Math.round(width * scale));
    const h = Math.max(1, Math.round(height * scale));
    if (canvas.width !== w) canvas.width = w;
    if (canvas.height !== h) canvas.height = h;
    context.drawImage(source, 0, 0, w, h);
    const pixels = context.getImageData(0, 0, w, h);
    return jsQR(pixels.data, w, h, { inversionAttempts: 'dontInvert' })?.data;
  };

  const handleText = (text) => {
    if (!active || !text) return false;
    let code = text;
    try { code = new URL(text).searchParams.get('qr') || text; } catch { /* код без URL */ }
    closeSheet();
    onSuccess(code);
    return true;
  };

  sheet.querySelector('#qrCancelBtn').addEventListener('click', closeSheet);

  const fileInput = sheet.querySelector('#qrFileInput');
  sheet.querySelector('#qrPickFile').addEventListener('click', () => fileInput.click());

  fileInput.addEventListener('change', async () => {
    const file = fileInput.files?.[0];
    if (!file) return;
    fileInput.value = '';
    const url = URL.createObjectURL(file);
    try {
      const image = new Image();
      image.src = url;
      await image.decode();
      if (!active || handleText(decode(image, image.naturalWidth, image.naturalHeight, 1600))) return;
    } catch { /* повреждённое или неподдерживаемое изображение */ }
    finally { URL.revokeObjectURL(url); }
    if (active) banner('Не удалось распознать QR-код на фото', true);
  });

  const video = sheet.querySelector('#qrVideo');
  const scanFrame = () => {
    if (!active) return;
    if (video.readyState >= 2 && handleText(decode(video, video.videoWidth, video.videoHeight, 960))) return;
    scanTimer = setTimeout(scanFrame, 150);
  };

  (async () => {
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error('Камера недоступна в этом браузере');
      }
      stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: { ideal: 'environment' } },
        audio: false,
      });
      if (!active) {
        stopCamera();
        return;
      }
      video.srcObject = stream;
      await video.play();
      scanTimer = setTimeout(scanFrame, 150);
    } catch (err) {
      stopCamera();
      if (active) {
        const vf = sheet.querySelector('.qr-viewfinder');
        vf.innerHTML = `<p class="empty" style="padding:20px; text-align:center;">${esc(err.message || 'Нет доступа к камере')}</p>`;
      }
    }
  })();
}
