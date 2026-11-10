// Учительский журнал — исключительно GET /api/teacher/journal*.
'use strict';

import { $, api, esc, formatAverage, showFailure, showLoading } from './core.js';

let filters;

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
function options(items, ids, names) {
  return items.map((item) => {
    const value = number(item, ids);
    return value ? '<option value="' + value + '">' + esc(label(item, names)) + '</option>' : '';
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
function selected(id) { return Number($(id).value) || 0; }
function yearId(years) {
  return number(filters, ['yearId']) || number(filters && filters.year, ['id', 'yearId']) ||
    number(years[0], ['id', 'yearId']);
}

function renderFilter() {
  const choices = data();
  const year = yearId(choices.years);
  $('teacherJournalContent').innerHTML =
    '<section class="teacher-journal"><h2>Классный журнал</h2>' +
    '<p class="teacher-hint">Только просмотр: оценки и посещаемость здесь не редактируются.</p>' +
    '<div class="teacher-filters">' +
    '<label>Класс<select id="teacherClass">' + options(choices.classes, ['id', 'classId'], ['name', 'className', 'title']) + '</select></label>' +
    '<label>Предмет<select id="teacherSubject">' + options(choices.subjects, ['id', 'subjectGroupId', 'sgId'], ['name', 'subjectName', 'title']) + '</select></label>' +
    '<label>Период<select id="teacherTerm">' + options(choices.terms, ['id', 'termId'], ['name', 'title']) + '</select></label>' +
    '<input id="teacherYear" type="hidden" value="' + year + '">' +
    '</div><button class="primary-btn" id="teacherLoad">Показать оценки</button>' +
    '<div id="teacherJournalResult"></div></section>';
  $('teacherLoad').addEventListener('click', loadJournal);
}

function renderJournal(payload) {
  const journal = payload && payload.journals && payload.journals[0];
  const box = $('teacherJournalResult');
  if (!journal) { box.innerHTML = '<p class="empty">В выбранном периоде оценок пока нет.</p>'; return; }
  const dates = new Map((journal.classMeeting || []).map((item) => [item.id, String(item.date || '').slice(5, 10)]));
  const works = (journal.assignments || []).filter((item) => !item.isNotAssigned).sort((a, b) =>
    (dates.get(a.classMeetingId) || '').localeCompare(dates.get(b.classMeetingId) || '') || a.id - b.id);
  const marks = new Map((journal.marks || []).map((item) => [item.studentId + ':' + item.assignmentId, item.mark]));
  const averages = new Map((journal.avgMarks || []).map((item) => [item.studentId, item.avgMark]));
  const head = works.map((item) => '<th title="' + esc(item.assignmentName || '') + '">' +
    esc(dates.get(item.classMeetingId) || '—') + '</th>').join('');
  const rows = (journal.students || []).map((student, index) => '<tr><th>' + (index + 1) + '. ' +
    esc(student.fullName) + '</th>' + works.map((item) => '<td>' +
    esc(String(marks.get(student.id + ':' + item.id) || '')) + '</td>').join('') +
    '<td class="teacher-average">' + (formatAverage(averages.get(student.id)) || '—') + '</td></tr>').join('');
  box.innerHTML = '<p class="teacher-caption">' + esc(journal.className || '') + ' · ' +
    esc(journal.subjectGroupName || '') + '</p><div class="teacher-table-wrap"><table class="teacher-table"><thead><tr><th>Ученик</th>' +
    head + '<th>Средний</th></tr></thead><tbody>' + rows + '</tbody></table></div>';
}

async function loadJournal() {
  const box = $('teacherJournalResult');
  const params = new URLSearchParams({
    class_id: selected('teacherClass'), subject_group_id: selected('teacherSubject'),
    term_id: selected('teacherTerm'), year_id: selected('teacherYear'),
  });
  if ([...params.values()].some((value) => !Number(value))) { box.textContent = 'Школа не передала параметры журнала.'; return; }
  showLoading(box);
  try { renderJournal((await api('/api/teacher/journal?' + params)).data); }
  catch (error) { showFailure(box, error); }
}

export async function loadTeacherJournal() {
  if (!filters) filters = (await api('/api/teacher/journal/filter')).data;
  const choices = data();
  if (!choices.classes.length || !choices.subjects.length || !choices.terms.length) {
    throw new Error('Учительский журнал для этого аккаунта недоступен');
  }
  renderFilter();
}
