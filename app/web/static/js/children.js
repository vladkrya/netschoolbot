// «Все дети»: сводка по каждому ребёнку аккаунта на одном экране — средний
// за период, свежие оценки, долги, слабые предметы и задания на завтра.
'use strict';

import { api, banner, dayMonth, errorCard, esc, formatAverage, plural, skeleton } from './core.js';
import { markBox } from './details.js';
import { pushScreen } from './screens.js';
import { notify, tap } from './haptics.js';

function childCard(child, index) {
  if (child.error) {
    return `<div class="card child-card"><div class="child-head"><b>${esc(child.name)}</b></div>
      <div class="muted small">${esc(child.error)}</div></div>`;
  }
  const debts = child.debts || [];
  const low = child.low || [];
  const hw = child.homework || { total: 0, left: 0 };
  return `<div class="card child-card">
      <div class="child-head">
        <div><b>${esc(child.name)}</b><div class="muted small">${esc(child.period || '')}${child.current ? ' · открыт сейчас' : ''}</div></div>
        <div class="child-avg">${child.average == null ? '—' : formatAverage(child.average)}<span class="muted small">средний</span></div>
      </div>
      ${child.recent?.length ? `<div class="child-marks">${child.recent.map((mark) =>
        `<span class="child-mark" title="${esc(`${mark.subject}, ${dayMonth(mark.date)}`)}">${markBox(mark.mark, '', mark.weight || 1)}</span>`).join('')}</div>
        <div class="muted small">последние: ${esc([...new Set(child.recent.map((mark) => mark.subject))].slice(0, 3).join(', '))}</div>`
        : '<div class="muted small">Оценок в этом периоде пока нет</div>'}
      <div class="child-facts">
        <span class="${debts.length ? 'bad' : ''}">${debts.length ? `🔴 ${debts.length} ${plural(debts.length, 'долг', 'долга', 'долгов')}: ${esc([...new Set(debts.map((d) => d.subject))].join(', '))}` : '✓ Долгов нет'}</span>
        ${low.length ? `<span class="warn">⚠️ Ниже «4»: ${low.map((s) => `${esc(s.subject)} ${formatAverage(s.average)}`).join(', ')}</span>` : ''}
        <span>${hw.total ? `📚 Д/З на сегодня и завтра: ${hw.left ? `не сделано ${hw.left} из ${hw.total}` : `все ${hw.total} сделаны`}` : '📚 Д/З на сегодня и завтра нет'}</span>
      </div>
      ${child.current ? '' : `<button class="btn outline child-open" data-child="${index}">Открыть дневник</button>`}
    </div>`;
}

export async function openChildren() {
  tap();
  const body = pushScreen('Все дети');
  body.innerHTML = skeleton(3);
  const load = async (refresh = false) => {
    try {
      const { data } = await api(`/api/children${refresh ? '?refresh=1' : ''}`);
      const children = data.children || [];
      body.innerHTML = children.length ? children.map(childCard).join('')
        : '<div class="empty big">В аккаунте один ребёнок</div>';
      body.querySelectorAll('[data-child]').forEach((button) => button.addEventListener('click', async () => {
        tap();
        button.disabled = true;
        try {
          await api('/api/student', { method: 'POST', body: JSON.stringify({ id: children[Number(button.dataset.child)].id }) });
          notify('success');
          location.reload();
        } catch (error) {
          notify('error');
          banner(error.message, true);
          button.disabled = false;
        }
      }));
    } catch (error) {
      body.innerHTML = errorCard(error, 'childrenRetry');
      body.querySelector('#childrenRetry')?.addEventListener('click', () => load(true));
    }
  };
  await load();
}
