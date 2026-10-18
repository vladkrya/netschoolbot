// Сглаживаем крупные сетевые пакеты, не замедляя весь ответ длинной анимацией.
export function textStream(render) {
  let target = [], shown = 0, timer = null, finished = null;
  function tick() {
    shown = Math.min(target.length, shown + Math.max(1, Math.ceil((target.length - shown) / 10)));
    render(target.slice(0, shown).join(''));
    if (shown < target.length) timer = setTimeout(tick, 16);
    else {
      timer = null;
      finished?.();
      finished = null;
    }
  }
  return {
    push(text) {
      target = Array.from(text); // Не разрезаем emoji пополам.
      shown = Math.min(shown, target.length);
      if (timer === null) timer = setTimeout(tick, 0);
    },
    async finish() {
      if (timer !== null) await new Promise(resolve => { finished = resolve; });
    },
    cancel() {
      clearTimeout(timer);
      timer = null;
      finished?.();
      finished = null;
    },
  };
}
