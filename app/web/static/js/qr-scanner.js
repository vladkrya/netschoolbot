// Сканер QR-кода через камеру устройства для веб-приложения (PWA / браузер)
'use strict';

import { banner, esc } from './core.js';
import { closeSheet, openSheet } from './sheet.js';

export function openWebQrScanner(onSuccess) {
  let stream = null;
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
      <input type="file" id="qrFileInput" accept="image/*" capture="environment" style="display:none">
      <div style="display:flex; justify-content:center; gap:10px; margin-top:12px;">
        <button class="btn outline" id="qrPickFile" type="button">Загрузить фото QR</button>
        <button class="btn" id="qrCancelBtn" type="button">Отмена</button>
      </div>
    </div>
  `;

  const sheet = openSheet('Сканирование QR-кода', html);

  const cleanup = () => {
    active = false;
    if (stream) {
      stream.getTracks().forEach((track) => track.stop());
      stream = null;
    }
  };

  const handleText = (text) => {
    let code = '';
    try {
      code = new URL(text).searchParams.get('qr') || text;
    } catch {
      code = text;
    }
    if (code) {
      cleanup();
      closeSheet();
      onSuccess(code);
      return true;
    }
    return false;
  };

  sheet.querySelector('#qrCancelBtn')?.addEventListener('click', () => {
    cleanup();
    closeSheet();
  });

  const fileInput = sheet.querySelector('#qrFileInput');
  sheet.querySelector('#qrPickFile')?.addEventListener('click', () => fileInput?.click());

  fileInput?.addEventListener('change', async (event) => {
    const file = event.target.files?.[0];
    if (!file) return;
    if ('BarcodeDetector' in window) {
      try {
        const detector = new BarcodeDetector({ formats: ['qr_code'] });
        const img = new Image();
        img.src = URL.createObjectURL(file);
        await img.decode();
        const barcodes = await detector.detect(img);
        if (barcodes.length && handleText(barcodes[0].rawValue)) {
          return;
        }
      } catch { /* fallback */ }
    }
    banner('Не удалось распознать QR-код на фото', true);
  });

  // Запуск камеры
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
        cleanup();
        return;
      }
      const video = sheet.querySelector('#qrVideo');
      if (!video) return;
      video.srcObject = stream;
      await video.play();

      if ('BarcodeDetector' in window) {
        const detector = new BarcodeDetector({ formats: ['qr_code'] });
        const scanFrame = async () => {
          if (!active) return;
          try {
            const barcodes = await detector.detect(video);
            if (barcodes.length && handleText(barcodes[0].rawValue)) {
              return;
            }
          } catch { /* frame skip */ }
          if (active) requestAnimationFrame(scanFrame);
        };
        requestAnimationFrame(scanFrame);
      }
    } catch (err) {
      if (active) {
        const vf = sheet.querySelector('.qr-viewfinder');
        if (vf) vf.innerHTML = `<p class="empty" style="padding:20px; text-align:center;">${esc(err.message || 'Нет доступа к камере')}</p>`;
      }
    }
  })();
}
