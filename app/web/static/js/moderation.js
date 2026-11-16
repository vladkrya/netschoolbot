import { api, banner, esc, showFailure } from './core.js';
import { popScreen, pushScreen, topScreen } from './screens.js';

function formatDate(iso) {
  return iso.split('-').reverse().join('.');
}
const root = '/api/moderation';
const reasons = { time: 'Неверное время', room: 'Неверный кабинет', subject: 'Неверный предмет', cancelled: 'Уточнить отмену' };
const post = (path, body) => api(root + path, { method: 'POST', body: JSON.stringify(body) });

function noticeDetail(text) {
  return esc(text).replace(/\b\d{2}:\d{2}–\d{2}:\d{2}\b/g, '<span class="moderation-time">$&</span>');
}

export function noticesHtml(notices = [], readonly = false) {
  return notices.map(({ id, text, title, detail, source }) => `<section class="card moderation-notice">
    <b>${noticeDetail(title || text)}</b>
    ${detail ? `<p class="moderation-detail">${noticeDetail(detail)}</p>` : ''}
    <p class="muted small">${esc(source || 'Изменение внесено в СГОшке')}</p>
    ${readonly ? '' : `<details><summary>Сообщить об ошибке</summary>
      <div class="chips">${Object.entries(reasons).map(([key, label]) =>
        `<button class="chip" type="button" data-report="${id}" data-reason="${key}">${label}</button>`).join('')}</div>
    </details>`}</section>`).join('');
}

export async function reportChange(id, reason) {
  await post(`/changes/${id}/report`, { reason });
}

function quickGrantForm() {
  return `<form class="card" data-form="quick-grant">
    <h3>Выдать модератора школы</h3>
    <label>Номер аккаунта СГОшки <input name="account_no" type="number" min="1" required></label>
    <button class="btn">Выдать модератора</button>
    <p class="muted small">Школу определим по аккаунту и подключим её расписание.</p>
    <p data-grant-result role="status" hidden></p>
  </form>`;
}

function schoolForm() {
  return `<form class="card" data-form="schools"><h3>Подключение школы</h3>
    <label>Номер аккаунта для поиска <input name="find_account" type="number" min="1"></label>
    <button type="button" class="chip" data-resolve-school>Найти школу</button>
    <label>Адрес СГО без протокола <input name="installation" required maxlength="250"></label>
    <label>ID школы в СГО <input name="school_id" type="number" min="1" required></label>
    <label>Название <input name="title" required maxlength="150"></label>
    <label><input name="enabled" type="checkbox" checked> Собирать расписание</label>
    <button class="chip">Сохранить</button></form>`;
}

function permissionForm(data, classes, role = {}) {
  const options = Object.entries(classes).map(([key, label]) =>
    `<option value="${esc(key)}" ${key === role.class_key ? 'selected' : ''}>${esc(label)}</option>`).join('');
  return `<form class="card" data-form="roles"><h3>${role.account_no ? `Права аккаунта #${role.account_no}` : 'Назначить модератора'}</h3>
    <label>Номер аккаунта <input name="account_no" type="number" min="1" value="${role.account_no || ''}" required></label>
    <label>Область <select name="class_key"><option value="">Вся школа</option>${options}</select></label>
    ${Object.entries(data.permissions).map(([key, label]) =>
      `<label class="switch-row"><span>${esc(label)}</span><input type="checkbox" name="permission" value="${key}"
        ${role.permissions?.includes(key) ? 'checked' : ''} ${role.class_key && key === 'bells' ? 'disabled' : ''}></label>`).join('')}
    <button class="chip">Сохранить права</button>
    ${role.account_no ? '<button class="chip" name="revoke" value="1">Отозвать права</button>' : ''}</form>`;
}

function shiftForm(classes) {
  return `<form class="card" data-form="shift"><h3>Смена класса</h3>
    <label>Класс <select name="class_key" required>${Object.entries(classes).map(([key, label]) =>
      `<option value="${esc(key)}">${esc(label)}</option>`).join('')}</select></label>
    <label>Смена <select name="shift"><option value="0">Неизвестна</option>
      <option value="1">Первая</option><option value="2">Вторая</option></select></label>
    <button class="chip">Сохранить</button></form>`;
}

function bellsForm(today) {
  return `<form class="card" data-form="bells"><h3>Звонки</h3>
    <label>С <input name="date" type="date" value="${esc(today)}" required></label>
    <label>По <input name="end_date" type="date"> <span class="muted small">Для одного дня оставьте пустым</span></label>
    <label><input name="school_days_only" type="checkbox" checked> Понедельник — пятница, если выбран период</label>
    <label>Смена <select name="shift"><option value="0">Все смены</option>
      <option value="1">Первая</option><option value="2">Вторая</option></select></label>
    <label>Сколько уроков оставить <input name="limit" type="number" min="0" max="20" placeholder="Все"></label>
    <details><summary>Задать время отдельных уроков</summary>
      ${Array.from({ length: 20 }, (_, index) => `<label>${index + 1}-й урок
        <input type="time" name="start${index + 1}" aria-label="Начало ${index + 1}-го урока">
        <input type="time" name="end${index + 1}" aria-label="Конец ${index + 1}-го урока">
        <small data-slot-info="${index + 1}" class="muted"></small></label>`).join('')}
    </details>
    <button class="chip">Проверить и опубликовать</button></form>`;
}

function prefillBells(form, data) {
  const date = form.elements.date.value;
  const shift = Number(form.elements.shift.value);
  const shifts = new Map((data.classes || []).map(item => [item.class_key, item.shift]));
  for (let number = 1; number <= 20; number += 1) {
    const pairs = new Set();
    for (const lesson of data.lessons) {
      if (lesson.date !== date || (shift && shifts.get(lesson.class_key) !== shift)) continue;
      const shown = lesson.effective || lesson;
      if (shown.number === number && shown.start && shown.end && !shown.moderation_cancelled) {
        pairs.add(shown.start + '|' + shown.end);
      }
    }
    const [start, end] = pairs.size === 1 ? [...pairs][0].split('|') : ['', ''];
    const from = form.elements['start' + number];
    const to = form.elements['end' + number];
    from.defaultValue = from.value = start;
    to.defaultValue = to.value = end;
    form.querySelector(`[data-slot-info="${number}"]`).textContent =
      pairs.size > 1 ? 'Разное время у классов' : pairs.size === 0 ? 'Нет данных' : '';
  }
}

function lessonForm(lesson, index, lessons) {
  const swaps = lessons.filter(other => other.date === lesson.date && other.class_key === lesson.class_key && other.number !== lesson.number);
  const swapForm = swaps.length ? `<form class="card" data-form="swap" data-lesson-index="${index}">
    <label>Поменять местами с уроком <select name="swap_key">${swaps.map(other =>
      `<option value="${esc(other.lesson_key)}">${other.number}. ${esc(other.subject)} · ${esc(other.start)}</option>`
    ).join('')}</select></label><button class="chip">Поменять местами</button></form>` : '';
  return `<form class="card" data-form="lesson" data-lesson-index="${index}">
    <label>Предмет <input name="subject" value="${esc(lesson.subject)}" maxlength="100" required></label>
    <label>Кабинет <input name="room" value="${esc(lesson.room || '')}" maxlength="100"></label>
    <label>Номер <input name="number" type="number" min="1" max="20" value="${lesson.number}" required></label>
    <label>Начало урока <input name="start" type="time" value="${esc(lesson.effective?.start || lesson.start || '')}"></label>
    <label>Конец урока <input name="end" type="time" value="${esc(lesson.effective?.end || lesson.end || '')}"></label>
    <label><input name="cancelled" type="checkbox"> Отменить урок</label>
    <label>Повторять каждую неделю по <input name="end_date" type="date"></label>
    <button class="chip">Опубликовать</button></form>${swapForm}`;
}

function lessonList(data, classes, selectedClass, selectedDate) {
  if (!Object.keys(classes).length) {
    return '<section class="card"><p>Чтобы класс появился здесь, ученик или родитель из него должен обновить дневник в СГОшке.</p></section>';
  }
  const instance = data.lessons.map((lesson, index) => ({ lesson, index }))
    .filter(({ lesson }) => lesson.class_key === selectedClass);
  const date = selectedDate || data.today;
  const onDay = instance.filter(({ lesson }) => lesson.date === date)
    .sort((a, b) => (a.lesson.effective || a.lesson).number - (b.lesson.effective || b.lesson).number);
  return `<section class="card">
    <div class="moderation-filters">
      <label>Класс <select data-class-picker>${Object.entries(classes).map(([key, label]) =>
        `<option value="${esc(key)}" ${key === selectedClass ? 'selected' : ''}>${esc(label)}</option>`
      ).join('')}</select></label>
      <div><span>Дата</span>
        <button type="button" class="chip" data-open-date aria-label="Выбрать дату, ${esc(formatDate(date))}">${esc(formatDate(date))}</button>
      </div>
    </div>
    ${onDay.length ? `<ul class="moderation-lessons">${onDay.map(({ lesson, index }) => {
      const shown = lesson.effective || lesson;
      const time = shown.moderation_cancelled ? 'Отменён' : [shown.start, shown.end].filter(Boolean).join('–');
      const room = (shown.room || '').match(/^(?:каб\.?\s*)?(\d+[а-яa-z]?)(?=\s|$)/i)?.[1];
      return `<li><button type="button" data-open-lesson="${index}">
        <span class="moderation-lesson-number">${shown.number}</span>
        <span><strong>${esc(shown.subject)}</strong><small>${esc([time, room ? `каб. ${room}` : shown.room].filter(Boolean).join(' · '))}</small></span>
        <span aria-hidden="true" class="muted">›</span>
      </button></li>`;
    }).join('')}</ul>` : '<p class="muted">На этот день расписание пока не получено.</p>'}
  </section>`;
}

function addLessonForm(data, classes) {
  const options = Object.entries(classes).filter(([key]) =>
    data.school_rights.includes('lessons') || key === data.scope.class_key);
  if (!options.length) return '';
  return `<form class="card" data-form="add"><h3>Добавить урок</h3>
    <label>Класс <select name="class_key">${options.map(([key, label]) =>
      `<option value="${esc(key)}">${esc(label)}</option>`).join('')}</select></label>
    <label>Дата <input name="date" type="date" required></label>
    <label>Предмет <input name="subject" maxlength="100" required></label>
    <label>Кабинет <input name="room" maxlength="100"></label>
    <label>Номер <input name="number" type="number" min="1" max="20" required></label>
    <label>Время <input name="start" type="time" aria-label="Начало" required>
      <input name="end" type="time" aria-label="Конец" required></label>
    <label>Повторять каждую неделю по <input name="end_date" type="date"></label>
    <button class="chip">Опубликовать</button></form>`;
}

function announceForm(data, classes) {
  const school = data.school_rights.includes('announce');
  const options = Object.entries(classes).filter(([key]) => school || key === data.scope.class_key)
    .map(([key, label]) => `<option value="${esc(key)}">${esc(label)}</option>`).join('');
  return `<form class="card" data-form="announce"><h3>Объявление</h3>
    <label>Дата <input name="date" type="date" required></label>
    <label>Для кого <select name="class_key">${school ? '<option value="">Вся школа</option>' : ''}${options}</select></label>
    <label>Текст <textarea name="text" maxlength="1000" required></textarea></label>
    <button class="chip">Опубликовать</button></form>`;
}

export function openModeration() {
  const panel = pushScreen('Модерация');
  panel.classList.add('moderation-panel');
  panel.innerHTML = '<p class="muted">Загрузка…</p>';
  let selection = '';
  let activeTab = 0;
  let selectedClass = '';
  let selectedDate = '';
  let classes = {};
  let data;
  let loadId = 0;
  const load = async () => {
    const id = ++loadId;
    try {
      const result = await api(root + (selection ? `?${selection}` : ''));
      if (id !== loadId) return;
      data = result.data;
      const scope = data.scope;
      classes = Object.fromEntries((data.classes || []).map(item => [item.class_key, item.class_label]));
      if (scope?.class_key) classes[scope.class_key] ||= scope.class_label;
      for (const role of data.roles || []) if (role.class_key) classes[role.class_key] ||= role.class_key;
      if (!Object.hasOwn(classes, selectedClass)) selectedClass = Object.keys(classes)[0] || '';
      const tabs = scope ? ['Расписание', 'Звонки и объявления', 'История', ...(data.owner ? ['Модераторы'] : [])] : [];
      panel.innerHTML = (data.owner ? quickGrantForm() + '<details class="card"><summary>Подключить школу вручную</summary>' + schoolForm() + '</details>' + `<label>Школа <select data-school aria-label="Школа">
        <option value="">Моя школа</option>${data.schools.map(school => {
          const query = new URLSearchParams({ installation: school.installation, school_id: school.school_id }).toString();
          return `<option value="${esc(query)}" ${query === selection ? 'selected' : ''}>${esc(school.title)}</option>`;
        }).join('')}</select></label>` : '') + (!scope ? `<p class="muted">${data.owner
        ? 'После выдачи прав школа появится в списке.'
        : 'Права модератора для твоей школы ещё не выданы.'}</p>` :
        `<div class="chips" aria-label="Разделы модерации">${tabs.map((label, index) =>
          `<button type="button" class="chip ${index === activeTab ? 'active' : ''}" aria-pressed="${index === activeTab}" data-tab-index="${index}">${label}</button>`).join('')}</div>
         <div data-pane="0" ${activeTab === 0 ? '' : 'hidden'}>${lessonList(data, classes, selectedClass, selectedDate)}</div>
         <div data-pane="1" ${activeTab === 1 ? '' : 'hidden'}>
           ${data.school_rights.includes('bells') ? bellsForm(data.today) : ''}
           ${[...data.school_rights, ...data.class_rights].includes('lessons') ? addLessonForm(data, classes) : ''}
           ${[...data.school_rights, ...data.class_rights].includes('announce') ? announceForm(data, classes) : ''}
         </div>
         <div data-pane="2" ${activeTab === 2 ? '' : 'hidden'} class="card"><h3>История и сообщения об ошибках</h3>
           ${data.history.map(item => `<p><b>${esc(formatDate(item.date))}${item.end_date !== item.date ? ` — ${esc(formatDate(item.end_date))}` : ''}</b> · ${esc(item.payload.text || item.payload.summary ||
             (item.payload.limit === null ? 'Изменены звонки' : `Количество уроков: ${item.payload.limit}`))}
             ${item.revoked_at ? ' · Отозвано' : item.can_revoke ? `<button class="chip" data-revoke="${item.id}">Отозвать</button>` : ''}
             ${item.reports?.map(report => `<br>${esc(reasons[report.reason])} · ${report.count}`).join('') || ''}</p>`).join('') || '<p>Правок пока нет.</p>'}
         </div>
         ${data.owner ? `<div data-pane="3" ${activeTab === 3 ? '' : 'hidden'}>${permissionForm(data, classes)}
           ${data.roles.map(role => permissionForm(data, classes, role)).join('')}
           ${Object.keys(classes).length ? shiftForm(classes) : ''}</div>` : ''}`);
      const bells = panel.querySelector('[data-form="bells"]');
      if (bells) prefillBells(bells, data);
    } catch (error) {
      if (id === loadId) showFailure(panel, error, load);
    }
  };
  panel.addEventListener('change', event => {
    if (event.target.matches('[data-form="bells"] [name="date"], [data-form="bells"] [name="shift"]')) {
      prefillBells(event.target.form, data);
      return;
    }
    if (event.target.matches('[data-class-picker]')) {
      selectedClass = event.target.value;
      panel.querySelector('[data-pane="0"]').innerHTML = lessonList(data, classes, selectedClass, selectedDate);
      panel.querySelector('[data-class-picker]').focus();
      return;
    }
    if (event.target.matches('[data-form="roles"] select[name="class_key"]')) {
      const bells = event.target.form.querySelector('[name="permission"][value="bells"]');
      bells.disabled = Boolean(event.target.value);
      if (bells.disabled) bells.checked = false;
    }
    if (event.target.matches('[data-school]')) {
      selection = event.target.value;
      activeTab = 0;
      load();
    }
  });
  panel.addEventListener('click', async event => {
    if (event.target.closest('[data-open-date]')) {
      const { openCalendar } = await import('./day.js');
      openCalendar(selectedDate || data.today, date => {
        selectedDate = date;
        panel.querySelector('[data-pane="0"]').innerHTML = lessonList(data, classes, selectedClass, selectedDate);
        panel.querySelector('[data-open-date]').focus();
      });
      return;
    }
    const lessonButton = event.target.closest('[data-open-lesson]');
    if (lessonButton) {
      const index = Number(lessonButton.dataset.openLesson);
      const lesson = data.lessons[index];
      const shown = lesson.effective || lesson;
      const canEdit = data.school_rights.includes('lessons') ||
        (lesson.class_key === data.scope.class_key && data.class_rights.includes('lessons'));
      const date = formatDate(lesson.date);
      const editor = pushScreen(shown.subject, `${classes[lesson.class_key]} · ${date} · ${shown.number}-й урок`);
      editor.classList.add('moderation-panel');
      if (canEdit) {
        editor.innerHTML = lessonForm(lesson, index, data.lessons);
        editor.addEventListener('submit', submit);
      } else {
        const time = shown.moderation_cancelled ? 'Урок отменён' : [shown.start, shown.end].filter(Boolean).join('–');
        editor.innerHTML = `<div class="card"><p>${esc(time)}</p><p>${esc(shown.room || 'Кабинет не указан')}</p></div>`;
      }
      return;
    }
    const resolve = event.target.closest('[data-resolve-school]');
    if (resolve) {
      const form = resolve.form;
      resolve.disabled = true;
      try {
        const { data: school } = await post('/schools/resolve', { account_no: Number(form.elements.find_account.value) });
        ['installation', 'school_id', 'title'].forEach(key => { form.elements[key].value = school[key]; });
      } catch (error) {
        banner(error.message);
      } finally {
        resolve.disabled = false;
      }
      return;
    }
    const tab = event.target.closest('[data-tab-index]');
    const revoke = event.target.closest('[data-revoke]');
    if (tab) {
      activeTab = Number(tab.dataset.tabIndex);
      panel.querySelectorAll('[data-pane]').forEach(pane => { pane.hidden = pane.dataset.pane !== tab.dataset.tabIndex; });
      panel.querySelectorAll('[data-tab-index]').forEach(button => {
        button.classList.toggle('active', button === tab);
        button.setAttribute('aria-pressed', String(button === tab));
      });
    }
    if (revoke && window.confirm('Отозвать эту правку?')) {
      try {
        await api(root + '/changes/' + revoke.dataset.revoke, { method: 'DELETE' });
        await load();
      } catch (error) { banner(error.message); }
    }
  });
  async function submit(event) {
    const form = event.target;
    const kind = form.dataset.form;
    if (!kind) return;
    event.preventDefault();
    const f = new FormData(form);
    const fields = Object.fromEntries(f);
    let path = '/changes';
    let payload = { ...data.scope, ...fields, kind };
    if (kind === 'quick-grant') {
      path = '/quick-grant';
      payload = { account_no: Number(fields.account_no) };
    } else if (kind === 'schools') {
      path = '/schools';
      payload = { ...fields, school_id: Number(fields.school_id), enabled: f.has('enabled') };
    } else if (kind === 'roles') {
      path = '/roles';
      payload = { ...payload, account_no: Number(fields.account_no),
        permissions: event.submitter?.name === 'revoke' ? [] : f.getAll('permission') };
    } else if (kind === 'shift') {
      path = '/shift';
      payload.shift = Number(fields.shift);
    } else if (kind === 'bells') {
      const slots = {};
      for (let number = 1; number <= 20; number += 1) {
        const pair = [fields['start' + number], fields['end' + number]];
        const from = form.elements['start' + number];
        const to = form.elements['end' + number];
        if (pair.some(Boolean) && (pair[0] !== from.defaultValue || pair[1] !== to.defaultValue)) slots[number] = pair;
      }
      const limit = fields.limit === '' ? null : Number(fields.limit);
      const shift = Number(fields.shift);
      const audience = ['Вся школа', 'Первая смена', 'Вторая смена'][shift];
      const changes = Object.entries(slots).map(([number, pair]) => `${number}-й урок: ${pair.join('–')}`);
      if (limit !== null) changes.push(limit ? `Уроки начиная с ${limit + 1}-го отменены` : 'Все уроки отменены');
      if (!changes.length) { banner('Укажите звонки или количество уроков'); return; }
      const period = fields.end_date && fields.end_date !== fields.date;
      const dateLabel = period ? `${fields.date} — ${fields.end_date}${f.has('school_days_only') ? ' (Пн–Пт)' : ''}` : fields.date;
      if (shift) changes.push("Правило применяется к классам с назначенной сменой");
      if (!window.confirm([dateLabel, audience, ...changes].join('\n'))) return;
      payload = { ...data.scope, kind, date: fields.date, end_date: fields.end_date,
        school_days_only: f.has('school_days_only'), class_key: '', shift, limit, slots };
    } else if (kind === 'add') {
      payload = { ...payload, kind: 'lesson', action: 'add', number: Number(fields.number) };
    } else if (kind === 'swap') {
      const lesson = data.lessons[Number(form.dataset.lessonIndex)];
      payload = { ...payload, kind: 'lesson', date: lesson.date, class_key: lesson.class_key,
        lesson_key: lesson.lesson_key, swap_key: fields.swap_key };
    } else if (kind === 'lesson') {
      const lesson = data.lessons[Number(form.dataset.lessonIndex)];
      payload = { ...payload, date: lesson.date, class_key: lesson.class_key, lesson_key: lesson.lesson_key,
        number: Number(fields.number), cancelled: f.has('cancelled') };
      if (payload.number === lesson.number && fields.start === form.elements.start.defaultValue &&
          fields.end === form.elements.end.defaultValue) {
        delete payload.start;
        delete payload.end;
      }
    }
    if (event.submitter) event.submitter.disabled = true;
    try {
      const result = await post(path, payload);
      if (kind === 'quick-grant') {
        selection = new URLSearchParams({
          installation: result.data.installation, school_id: result.data.school_id,
        }).toString();
        activeTab = 3;
        await load();
        banner(`Права выданы · ${result.data.title}`);
        return;
      }
      if (kind === 'schools') selection = new URLSearchParams({
        installation: payload.installation.trim().toLowerCase(), school_id: payload.school_id,
      }).toString();
      if ((kind === 'lesson' || kind === 'swap') && topScreen()?.contains(form)) popScreen();
      banner('Сохранено');
      await load();
    } catch (error) {
      const status = form.querySelector('[data-grant-result]');
      if (status) {
        status.textContent = error.message;
        status.hidden = false;
      } else {
        banner(error.message);
      }
    } finally {
      if (event.submitter) event.submitter.disabled = false;
    }
  }
  panel.addEventListener('submit', submit);
  load();
}
