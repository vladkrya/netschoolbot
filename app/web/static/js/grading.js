// Правила итоговой оценки — одни для «Итогов», «что если» и калькулятора баллов.
//
// Раньше калькулятор считал по своему «от 4,5 — пятёрка», а «Итоги» — по
// порогам пользователя, и два экрана давали разные ответы на один вопрос.
'use strict';

import { DEFAULT_THRESHOLDS, weightedAverage } from './core.js';
import { getPref } from './prefs.js';

// С какого среднего ставится «5», «4», «3». Общие пороги на все предметы,
// у отдельных предметов могут быть свои: { all: {5, 4, 3}, subjects: { «Алгебра»: {…} } }.

/** Пороги без ошибок: числа от 1 до 5, и «5» строго выше «4», «4» выше «3». */
export function validThresholds(value) {
  if (!value || typeof value !== 'object') return null;
  const t = { 5: Number(value[5]), 4: Number(value[4]), 3: Number(value[3]) };
  const ok = [5, 4, 3].every((g) => Number.isFinite(t[g]) && t[g] >= 1 && t[g] <= 5) && t[5] > t[4] && t[4] > t[3];
  return ok ? t : null;
}

export function thresholdPrefs() {
  const saved = getPref('thresholds');
  const subjects = saved?.subjects && typeof saved.subjects === 'object' ? saved.subjects : {};
  return { all: validThresholds(saved?.all), subjects };
}

export function thresholdsFor(subject) {
  const prefs = thresholdPrefs();
  return validThresholds(prefs.subjects[subject]) || prefs.all || DEFAULT_THRESHOLDS;
}

export const formatThreshold = (value) => String(value).replace('.', ',');

/** «5» от 4,5 · «4» от 3,5 · «3» от 2,5 — для подписи под расчётом. */
export function thresholdsText(subject) {
  const t = thresholdsFor(subject);
  return `«5» от ${formatThreshold(t[5])} · «4» от ${formatThreshold(t[4])} · «3» от ${formatThreshold(t[3])}`;
}

/** Сколько оценок `value` с весом `weight` нужно, чтобы среднее дошло до `target`. */
export function neededMarks(marks, target, value = 5, weight = 1) {
  const current = weightedAverage(marks);
  if (current != null && current >= target) return 0;
  const extra = [];
  // Предел на случай недостижимой цели.
  while (extra.length < 100) {
    extra.push({ mark: value, weight });
    if (weightedAverage([...marks, ...extra]) >= target) return extra.length;
  }
  return null;
}
