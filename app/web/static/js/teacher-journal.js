// Учительский журнал — исключительно GET /api/teacher/journal*.
'use strict';

import { $, api, avgClass, esc, formatAverage, showFailure, showLoading, skeleton } from './core.js';

let filters;
let lastPayload;
let lastUpdated;

function formatUpdated(timestamp) {
  if (!timestamp) return '';
  const d = new Date(timestamp);
  const now = new Date();
  const time = d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) {
    return `Обновлено в ${time}`;
  }
  return `Обновлено ${d.toLocaleDateString('ru-RU', { day: 'numeric', month: 'short' })}, ${time}`;
}

function list(data, names) {
  for (const name of names) if (Array.isArray(data && data[name])) return data[name];
  return [];
}
function number(item, names) {
  for (const name of names) if (Number.isFinite(Number(item && item[name]))) return Number(item[name]);
  return 0;
}
function label(item, names) {
  for (const name of names) if (item && item[name]) return String(item[name]);
  return '';
}
function options(items, ids, names, preferred) {
  let targetId = null;
  const prefStr = preferred != null ? String(preferred).toLowerCase() : '';
  if (prefStr) {
    for (const item of items) {
      const val = number(item, ids);
      const lbl = label(item, names).toLowerCase();
      if (String(val) === prefStr || lbl === prefStr || lbl.includes(prefStr)) {
        targetId = val;
        break;
      }
    }
  }
  if (targetId === null && items.length) {
    targetId = number(items[0], ids);
  }
  let selectedFound = false;
  return items.map((item) => {
    const value = number(item, ids);
    const isSel = value === targetId && !selectedFound;
    if (isSel) selectedFound = true;
    return value ? '<option value="' + value + '"' + (isSel ? ' selected' : '') + '>' + esc(label(item, names)) + '</option>' : '';
  }).join('');
}
function data() {
  return {
    classes: list(filters, ['classes', 'classGroups', 'classList']),
    subjects: list(filters, ['subjectGroups', 'subjects', 'subjectList']),
    terms: list(filters, ['terms', 'periods']),
    years: list(filters, ['years', 'schoolYears']),
  };
}
function selected(id) { return Number($(id)?.value) || 0; }
function yearId(years) {
  return number(filters, ['yearId']) || number(filters && filters.year, ['id', 'yearId']) ||
    number(years[0], ['id', 'yearId']);
}

function formatDate(dateStr) {
  if (!dateStr) return '—';
  const parts = String(dateStr).split('T')[0].split('-');
  if (parts.length === 3) {
    const [, m, d] = parts;
    return `${d.padStart(2, '0')}.${m.padStart(2, '0')}`;
  }
  return String(dateStr).slice(5, 10);
}

function renderFilter() {
  const choices = data();
  const year = yearId(choices.years);
  const defs = filters?.defaults || {};
  const prefClass = defs.classId || '5а';
  const prefSubject = defs.subjectId || 'алгебра';
  const prefTerm = defs.termId || null;

  function subjectsFor(cid) {
    const key = String(cid);
    if (filters?.subjectsByClass && filters.subjectsByClass[key]?.length) {
      return filters.subjectsByClass[key];
    }
    return choices.subjects;
  }

  $('teacherJournalContent').innerHTML =
    '<section class="teacher-journal"><h2>Классный журнал</h2>' +
    '<p class="teacher-hint">Только просмотр без редактирования (редактирование оценок появится в будущих обновлениях).</p>' +
    '<div class="teacher-filters">' +
    '<label>Класс<select id="teacherClass">' + options(choices.classes, ['id', 'classId'], ['name', 'className', 'title'], prefClass) + '</select></label>' +
    '<label>Предмет<select id="teacherSubject"></select></label>' +
    '<label>Период<select id="teacherTerm">' + options(choices.terms, ['id', 'termId'], ['name', 'title'], prefTerm) + '</select></label>' +
    '<input id="teacherYear" type="hidden" value="' + year + '">' +
    '</div><button class="primary-btn" id="teacherLoad">Показать оценки</button>' +
    '<div id="teacherJournalResult"></div></section>';

  function updateSubjects(preferred) {
    const cid = selected('teacherClass');
    const items = subjectsFor(cid);
    $('teacherSubject').innerHTML = options(items, ['id', 'subjectGroupId', 'sgId'], ['name', 'subjectName', 'title'], preferred);
  }

  updateSubjects(prefSubject);

  $('teacherLoad').addEventListener('click', loadJournal);
  $('teacherClass').addEventListener('change', () => {
    updateSubjects();
    loadJournal();
  });
  $('teacherSubject').addEventListener('change', loadJournal);
  $('teacherTerm').addEventListener('change', loadJournal);
}

function renderJournal(payload) {
  const journal = payload && payload.journals && payload.journals[0];
  const box = $('teacherJournalResult');
  if (!box) return;
  if (!journal) { box.innerHTML = '<p class="empty">В выбранном периоде оценок пока нет.</p>'; return; }
  const dates = new Map((journal.classMeeting || []).map((item) => [item.id, formatDate(item.date)]));
  const works = (journal.assignments || []).filter((item) => !item.isNotAssigned).sort((a, b) =>
    (dates.get(a.classMeetingId) || '').localeCompare(dates.get(b.classMeetingId) || '') || a.id - b.id);
  const marks = new Map((journal.marks || []).map((item) => [item.studentId + ':' + item.assignmentId, item.mark]));
  const averages = new Map((journal.avgMarks || []).map((item) => [item.studentId, item.avgMark]));
  const head = works.map((item, colIdx) => '<th data-col="' + colIdx + '" title="' + esc(item.assignmentName || '') + '">' +
    esc(dates.get(item.classMeetingId) || '—') + '</th>').join('');
  const rows = (journal.students || []).map((student, rowIndex) => {
    const avgVal = averages.get(student.id);
    const avgCls = avgVal != null ? avgClass(avgVal) : '';
    const fullName = student.fullName || student.name || '';
    return '<tr data-row="' + rowIndex + '"><th class="teacher-student" title="' + esc(fullName) + '">' + (rowIndex + 1) + '. ' +
      esc(fullName) + '</th>' + works.map((item, colIdx) => '<td class="teacher-cell" data-col="' + colIdx + '">' +
      esc(String(marks.get(student.id + ':' + item.id) || '')) + '</td>').join('') +
      '<td class="teacher-average ' + avgCls + '">' + (formatAverage(avgVal) || '—') + '</td></tr>';
  }).join('');
  const updatedText = formatUpdated(lastUpdated || Date.now());
  box.innerHTML = '<div class="teacher-caption-row"><p class="teacher-caption">' + esc(journal.className || '') + ' · ' +
    esc(journal.subjectGroupName || '') + '</p>' + (updatedText ? '<span class="teacher-updated">' + esc(updatedText) + '</span>' : '') +
    '</div><div class="teacher-table-wrap" data-own-gesture="true"><table class="teacher-table"><thead><tr><th>Ученик</th>' +
    head + '<th>Средний</th></tr></thead><tbody>' + rows + '</tbody></table></div>';

  const table = box.querySelector('.teacher-table');
  if (table) {
    table.addEventListener('click', (event) => {
      const target = event.target;
      const studentTh = target.closest('.teacher-student');
      const cellTd = target.closest('.teacher-cell');

      if (studentTh) {
        const tr = studentTh.closest('tr');
        const wasSelected = tr.classList.contains('row-selected') && !table.querySelector('.cell-selected');
        table.querySelectorAll('.row-selected').forEach((el) => el.classList.remove('row-selected'));
        table.querySelectorAll('.col-selected').forEach((el) => el.classList.remove('col-selected'));
        table.querySelectorAll('.cell-selected').forEach((el) => el.classList.remove('cell-selected'));
        if (!wasSelected) {
          tr.classList.add('row-selected');
        }
        return;
      }

      if (cellTd) {
        const tr = cellTd.closest('tr');
        const col = cellTd.dataset.col;
        const headerTh = table.querySelector('thead th[data-col="' + col + '"]');
        const wasSelected = cellTd.classList.contains('cell-selected');

        table.querySelectorAll('.row-selected').forEach((el) => el.classList.remove('row-selected'));
        table.querySelectorAll('.col-selected').forEach((el) => el.classList.remove('col-selected'));
        table.querySelectorAll('.cell-selected').forEach((el) => el.classList.remove('cell-selected'));

        if (!wasSelected) {
          tr.classList.add('row-selected');
          cellTd.classList.add('cell-selected');
          if (headerTh) headerTh.classList.add('col-selected');
        }
        return;
      }
    });
  }
}

async function loadJournal() {
  const box = $('teacherJournalResult');
  if (!box) return;
  const classId = selected('teacherClass');
  const subjectId = selected('teacherSubject');
  const termId = selected('teacherTerm');
  const yearIdVal = selected('teacherYear');
  if (!classId || !subjectId || !termId) {
    box.innerHTML = '<p class="empty">Выберите класс, предмет и период.</p>';
    return;
  }
  const classItem = data().classes.find((c) => number(c, ['id', 'classId']) === classId);
  const isIup = Boolean(classItem && (classItem.isIup || String(classItem.name || '').includes('*')));
  const params = new URLSearchParams({
    class_id: classId,
    subject_group_id: subjectId,
    term_id: termId,
    year_id: yearIdVal,
    ...(isIup ? { iup: '1' } : {}),
  });
  showLoading(box, skeleton(5));
  try {
    const res = await api('/api/teacher/journal?' + params);
    lastPayload = res.data;
    lastUpdated = Date.now();
    renderJournal(res.data);
  } catch (error) {
    showFailure(box, error, '<p class="empty">' + esc(error.message || 'Не удалось загрузить журнал') + '</p>');
  }
}

export async function loadTeacherJournal() {
  const container = $('teacherJournalContent');
  if (!container) return;
  if (filters && lastPayload) {
    if (!$('teacherClass')) {
      renderFilter();
    }
    renderJournal(lastPayload);
    return;
  }
  showLoading(container, skeleton(6));
  try {
    if (!filters) {
      filters = (await api('/api/teacher/journal/filter')).data;
    }
    const choices = data();
    if (!choices.classes.length || !choices.subjects.length || !choices.terms.length) {
      container.innerHTML = '<section class="teacher-journal"><h2>Классный журнал</h2><p class="empty">Учительский журнал для этого аккаунта недоступен.</p></section>';
      return;
    }
    renderFilter();
    await loadJournal();
  } catch (error) {
    showFailure(container, error, '<p class="empty">' + esc(error.message || 'Ошибка загрузки') + '</p>');
  }
}
