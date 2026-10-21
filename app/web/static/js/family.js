// Родные: приглашения, права доступа и отзыв.
'use strict';

import { $, api, banner, errorCard, esc, initData, isReadOnly, skeleton, tg } from './core.js';
import { pushScreen } from './screens.js';
import { notify, select, tap } from './haptics.js';

function rightsLabel(item) {
  if (item.notify && item.view) return 'Уведомления и дневник';
  return item.notify ? 'Только уведомления' : 'Только просмотр дневника';
}

/** Родные: приглашение с выбором, что доступно (уведомления, просмотр
 * дневника или оба), их права и отзыв; и к кому есть доступ у вас самих. */
export async function openFamily() {
  tap();
  const screen = pushScreen('Родные');
  screen.innerHTML = skeleton(3);
  const send = async (path, body) => {
    try {
      const { data: fresh } = await api(path, { method: 'POST', body: JSON.stringify(body) });
      notify('success');
      draw(fresh);
    } catch (error) {
      notify('error');
      banner(error.message, true);
    }
  };
  const draw = (data) => {
    screen.innerHTML = `${data.can_invite ? `<div class="card">
        <p>Пригласите маму, папу, бабушку — любого, кому важно знать об успехах ученика.
        Ссылка ведёт на сайт: там можно войти через Telegram или зарегистрироваться по почте.</p>
        <div class="family-rights" role="radiogroup" aria-label="Что будет доступно">
          <label><input type="radio" name="familyRights" value="both" checked> Уведомления и просмотр дневника</label>
          <label><input type="radio" name="familyRights" value="notify"> Только уведомления</label>
          <label><input type="radio" name="familyRights" value="view"> Только просмотр дневника</label>
        </div>
        <p class="muted small">Просмотр — оценки, расписание и задания в приложении. Менять ничего нельзя,
        почта и чаты не видны, пароль от «Сетевого города» не нужен. Доступ можно изменить или отозвать в любой момент.</p>
        <button class="btn" id="familyInvite">Пригласить по ссылке</button>
        <div class="family-link" id="familyLink" hidden></div>
      </div>
      <div class="card"><div class="card-title">Приглашённые</div>${data.followers.length ? data.followers.map((f) =>
        `<div class="family-row"><span class="family-name">${esc(f.name || `ID ${f.id}`)}<br><span class="muted small">${rightsLabel(f)}</span></span>
          <button class="btn outline small" data-drop="${f.id}">Убрать</button>
          <div class="family-row-rights">
            <button class="chip ${f.notify ? 'active' : ''}" data-right="notify" data-id="${f.id}" aria-pressed="${f.notify}">Уведомления</button>
            <button class="chip ${f.view ? 'active' : ''}" data-right="view" data-id="${f.id}" aria-pressed="${f.view}">Дневник</button>
          </div></div>`).join('')
        : '<p class="muted small">Пока никого. Здесь появится тот, кто перейдёт по ссылке и примет приглашение.</p>'}</div>` : ''}
      ${data.following.length ? `<div class="card"><div class="card-title">Вас пригласили</div>${data.following.map((f) =>
        `<div class="family-row"><span class="family-name">${esc(f.name)}<br><span class="muted small">${rightsLabel(f)}</span></span>
          <button class="btn outline small" data-leave="${f.id}">Отказаться</button></div>`).join('')}</div>` : ''}
      ${!data.can_invite && !data.following.length ? '<div class="card"><p class="muted">Пригласить родных можно после входа в «Сетевой город».</p></div>' : ''}`;
    $('familyInvite')?.addEventListener('click', async (event) => {
      select();
      const button = event.currentTarget;
      const choice = screen.querySelector('[name="familyRights"]:checked')?.value || 'both';
      button.disabled = true;
      try {
        const { data: invite } = await api('/api/family/invite', {
          method: 'POST', body: JSON.stringify({ notify: choice !== 'view', view: choice !== 'notify' }),
        });
        const box = $('familyLink');
        box.hidden = false;
        // Одна кнопка: системное меню «Поделиться» (там и мессенджеры, и
        // «Скопировать»), а где его нет — копирование. В Telegram ссылку не
        // уводим: объект Telegram.WebApp есть и в установленном приложении,
        // и раньше «Поделиться» открывал Telegram даже там.
        const text = 'Приглашение в школьный дневник';
        const canShare = Boolean(navigator.share) || Boolean(initData && tg?.openTelegramLink);
        box.innerHTML = `<p class="muted small">Ссылка одноразовая и действует сутки. Нажмите на неё, чтобы скопировать.</p>
          <input class="field family-url" readonly value="${esc(invite.url)}">
          <button class="btn" id="familySend">${canShare ? 'Отправить ссылку' : 'Скопировать ссылку'}</button>`;
        const copy = async () => {
          try { await navigator.clipboard.writeText(invite.url); notify('success'); banner('Ссылка скопирована', false, 2500); }
          catch { box.querySelector('.family-url').select(); }
        };
        box.querySelector('.family-url').addEventListener('click', copy);
        $('familySend').addEventListener('click', () => {
          tap();
          if (navigator.share) navigator.share({ url: invite.url, text }).catch(() => {});
          else if (initData && tg?.openTelegramLink) tg.openTelegramLink(`https://t.me/share/url?url=${encodeURIComponent(invite.url)}&text=${encodeURIComponent(text)}`);
          else copy();
        });
        button.textContent = 'Новая ссылка';
        button.classList.add('outline');
      } catch (error) {
        notify('error');
        banner(error.message, true);
      } finally {
        button.disabled = false;
      }
    });
    screen.querySelectorAll('[data-right]').forEach((chip) => chip.addEventListener('click', () => {
      tap();
      const follower = data.followers.find((f) => f.id === Number(chip.dataset.id));
      if (!follower) return;
      const next = { notify: follower.notify, view: follower.view, [chip.dataset.right]: !follower[chip.dataset.right] };
      if (!next.notify && !next.view && !confirm(`Убрать ${follower.name || 'приглашённого'}? Без уведомлений и дневника доступа не останется.`)) return;
      send('/api/family/rights', { id: follower.id, ...next });
    }));
    screen.querySelectorAll('[data-drop], [data-leave]').forEach((button) => button.addEventListener('click', () => {
      tap();
      const leave = 'leave' in button.dataset;
      if (leave && !confirm('Отказаться от доступа? Снова получить его можно только по новому приглашению.')) return;
      send('/api/family/remove', { id: Number(button.dataset.drop || button.dataset.leave), leave })
        // Отказались от дневника, который сейчас открыт, — назад к своему.
        .then(() => { if (leave && isReadOnly()) location.reload(); });
    }));
  };
  try {
    draw((await api('/api/family')).data);
  } catch (error) {
    screen.innerHTML = errorCard(error);
  }
}
