// Линейные иконки в духе официального приложения — вместо эмодзи, которые
// на каждой платформе рисуются по-своему и не красятся в цвет темы.
'use strict';

const svg = (body, size = 24) =>
  `<svg class="icon" width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${body}</svg>`;

export const ICONS = {
  trophy: svg('<path d="M8 4h8v5a4 4 0 0 1-8 0V4z"/><path d="M8 6H5a3 3 0 0 0 3 4M16 6h3a3 3 0 0 1-3 4M12 13v4M8.5 20h7M10 17h4"/>'),
  doc: svg('<path d="M7 3.5h7l4 4V20a.5.5 0 0 1-.5.5h-10A.5.5 0 0 1 7 20V3.5z"/><path d="M14 3.5V8h4M9.5 12h5M9.5 15.5h5"/>'),
  clock: svg('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 2"/>'),
  // День: листок календаря с одним отмеченным числом — не путать с
  // «Неделей» (calendar, сетка) и «Звонками» (clock).
  today: svg('<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4"/><rect x="7.5" y="13" width="4" height="4" rx="1"/>'),
  checklist: svg('<path d="m4 7 1.8 1.8L9 5.5M4 16l1.8 1.8L9 14.5M12.5 7.5H20M12.5 16.5H20"/>'),
  mail: svg('<rect x="3" y="5.5" width="18" height="13" rx="2.5"/><path d="m3.5 7.5 8.5 6 8.5-6"/>'),
  calendar: svg('<rect x="3.5" y="5" width="17" height="15.5" rx="2.5"/><path d="M3.5 10h17M8 3v4M16 3v4M7.5 14h2M11 14h2M14.5 14h2M7.5 17.5h2M11 17.5h2"/>'),
  users: svg('<circle cx="9" cy="8" r="3.2"/><path d="M3 19c.6-3.2 3-5 6-5s5.4 1.8 6 5"/><circle cx="17" cy="9" r="2.4"/><path d="M16 14.2c2.6.2 4.4 1.8 5 4.3"/>'),
  table: svg('<rect x="4" y="4" width="16" height="16" rx="2"/><path d="M4 10h16M4 15h16M10 4v16"/>'),
  book: svg('<path d="M12 6.5C10.5 5 8 4.5 4 4.5v14c4 0 6.5.5 8 2 1.5-1.5 4-2 8-2v-14c-4 0-6.5.5-8 2z"/><path d="M12 6.5v14"/>'),
  menu: svg('<path d="M4 7h16M4 12h16M4 17h16"/>'),
  bell: svg('<path d="M6 10a6 6 0 0 1 12 0c0 5 2 6 2 6H4s2-1 2-6"/><path d="M10 20a2 2 0 0 0 4 0"/>'),
  back: svg('<path d="M19 12H5M11 6l-6 6 6 6"/>'),
  left: svg('<path d="m15 5-7 7 7 7"/>'),
  right: svg('<path d="m9 5 7 7-7 7"/>'),
  down: svg('<path d="m6 9 6 6 6-6"/>', 16),
  clip: svg('<path d="m20 11.5-8.2 8.2a5 5 0 0 1-7-7L13 4.5a3.3 3.3 0 0 1 4.7 4.7l-8.2 8.3a1.7 1.7 0 0 1-2.4-2.4L14.8 7.4"/>', 16),
  chat: svg('<path d="M20 12a8 8 0 0 1-11.6 7.1L4 20l1-4.2A8 8 0 1 1 20 12z"/>', 20),
  homework: svg('<path d="m4 7 1.8 1.8L9 5.5M4 16l1.8 1.8L9 14.5M12.5 7.5H20M12.5 16.5H20"/>', 20),
  plus: svg('<path d="M12 5v14M5 12h14"/>'),
  // Для подсказки об установке: кнопки Safari и Chrome как они выглядят.
  share: svg('<path d="M12 3v12M8 7l4-4 4 4"/><path d="M8 10H6.5A1.5 1.5 0 0 0 5 11.5v8A1.5 1.5 0 0 0 6.5 21h11a1.5 1.5 0 0 0 1.5-1.5v-8a1.5 1.5 0 0 0-1.5-1.5H16"/>', 18),
  dots: svg('<circle cx="6" cy="12" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="18" cy="12" r="1.3"/>', 18),
  kebab: svg('<circle cx="12" cy="6" r="1.3"/><circle cx="12" cy="12" r="1.3"/><circle cx="12" cy="18" r="1.3"/>', 18),
  addSquare: svg('<rect x="4" y="4" width="16" height="16" rx="4"/><path d="M12 8.5v7M8.5 12h7"/>', 18),
  install: svg('<rect x="6" y="2.5" width="12" height="19" rx="2.5"/><path d="M12 8v6M9.5 11.5 12 14l2.5-2.5"/>', 18),
  send: svg('<path d="M4 12 20 4l-4 16-4-7z"/><path d="m12 13 8-9"/>'),
  close: svg('<path d="M6 6l12 12M18 6 6 18"/>', 20),
  shield: svg('<path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6Z"/><rect x="9.5" y="11" width="5" height="4" rx="1"/><path d="M10.5 11V9.8a1.5 1.5 0 0 1 3 0V11"/>', 20),
  calc: svg('<rect x="5" y="3" width="14" height="18" rx="2"/><path d="M8 7h8M8 11h2M11 11h2M14 11h2M8 14h2M11 14h2M14 14h2M8 17h2M11 17h2M14 17h2"/>', 20),
  search: svg('<circle cx="11" cy="11" r="6.5"/><path d="m16 16 4 4"/>', 18),
  // Две звёздочки-искры, а не лучи: лучи по кругу читались как значок загрузки.
  sparkles: svg('<path d="M10 3.5c.6 3.6 2.4 5.4 6 6-3.6.6-5.4 2.4-6 6-.6-3.6-2.4-5.4-6-6 3.6-.6 5.4-2.4 6-6z"/><path d="M18 14.5c.3 1.6 1 2.3 2.5 2.5-1.5.3-2.2 1-2.5 2.5-.3-1.5-1-2.2-2.5-2.5 1.5-.2 2.2-.9 2.5-2.5z"/>', 20),
  gear: svg('<circle cx="12" cy="12" r="3"/><path d="M12 2.8v2.4M12 18.8v2.4M2.8 12h2.4M18.8 12h2.4M5.5 5.5l1.7 1.7M16.8 16.8l1.7 1.7M5.5 18.5l1.7-1.7M16.8 7.2l1.7-1.7"/><circle cx="12" cy="12" r="6.3"/>'),
  pin: svg('<path d="M9 4h6l-1 5 3 3v2H7v-2l3-3z"/><path d="M12 14v6"/>', 18),
  up: svg('<path d="m6 15 6-6 6 6"/>', 18),
  palette: svg('<path d="M12 3a9 9 0 0 0 0 18c1.1 0 2-.9 2-2 0-.5-.2-1-.5-1.3-.3-.4-.5-.8-.5-1.2 0-1.1.9-2 2-2h2.3A4.7 4.7 0 0 0 21 9.8C21 6 17 3 12 3z"/><circle cx="7.5" cy="11" r="1"/><circle cx="10" cy="7" r="1"/><circle cx="15" cy="7" r="1"/>', 20),
};
