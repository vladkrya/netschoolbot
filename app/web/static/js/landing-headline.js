// Read the browser's line breaks for the completed phrase. Keeping those
// breaks while typing prevents a partially entered word from jumping lines.
export function layoutLandingPhrase(headline, phrase) {
  const probe = document.createElement('span');
  probe.className = 'typewriter-screen ld-headline-probe';
  probe.setAttribute('aria-hidden', 'true');
  probe.textContent = phrase;
  headline.append(probe);
  const range = document.createRange();
  const breaks = new Set();
  let previousTop;

  for (const word of phrase.matchAll(/[^ ]+/g)) {
    range.setStart(probe.firstChild, word.index);
    range.setEnd(probe.firstChild, word.index + 1);
    const { top } = range.getBoundingClientRect();
    if (word.index > 0 && previousTop !== undefined && top > previousTop + 1) {
      breaks.add(word.index - 1);
    }
    previousTop = top;
  }
  probe.remove();
  return phrase.split('').map((char, index) => breaks.has(index) ? '\n' : char).join('');
}

// The helper acts out line breaks. The heading stays in normal flow,
// so moving its lower edge also moves the content that follows it.
export function landingHeadlineScene(root, headline) {
  const hero = headline.closest('.ld-hero');
  const actor = hero.querySelector('.ld-helper');
  const arms = actor.querySelectorAll('.ld-helper-arm');
  const legs = actor.querySelectorAll('.ld-helper-leg');
  const lead = hero.querySelector('.ld-lead');
  let probe, probeText, width, startingHeight, closing;

  const pose = (x, y, angle = 0, scale = 'var(--ld-helper-scale, 1)') =>
    `translate(${x}px, ${y}px) rotate(${angle}deg) scale(${scale})`;
  const behindIcon = pose(8, 1, 0, .76);
  const transform = (...values) => values.map(value => ({ transform: value }));
  const visible = () => {
    const text = headline.getBoundingClientRect();
    const view = root.getBoundingClientRect();
    return text.bottom > view.top && text.top < view.bottom;
  };

  function finish() {
    headline.style.removeProperty('height');
    headline.classList.remove('ld-helper-closing');
    actor.hidden = true;
    probe?.remove();
    probe = probeText = null;
  }

  // Advance the layout and the character on the same clock. A scroll away
  // freezes the exact frame, including the height applied to the heading.
  function play(duration, tracks) {
    const animations = tracks.map(([node, frames]) => {
      const animation = node.animate(frames, {
        duration,
        easing: 'cubic-bezier(.42, 0, .25, 1)',
        fill: 'both',
      });
      animation.pause();
      animation.currentTime = 0;
      return animation;
    });
    return new Promise(resolve => {
      let elapsed = 0;
      let previous;
      function frame(now) {
        if (!headline.isConnected || headline.clientWidth !== width) {
          animations.forEach(animation => animation.cancel());
          finish();
          resolve([]);
          return;
        }
        const onScreen = visible();
        if (!onScreen) {
          previous = undefined;
          setTimeout(() => requestAnimationFrame(frame), 150);
          return;
        }
        if (previous !== undefined) elapsed += now - previous;
        previous = now;
        animations.forEach(animation => { animation.currentTime = Math.min(elapsed, duration); });
        if (elapsed < duration) requestAnimationFrame(frame);
        else resolve(animations);
      }
      requestAnimationFrame(frame);
    });
  }

  const gait = (limbs, angle) => [...limbs].map((limb, index) => {
    const swing = angle * (index === 0 ? 1 : -1);
    return [limb, transform(...[0, swing, -swing, swing, -swing, 0].map(deg => `rotate(${deg}deg)`))];
  });

  async function closeScene(before, after, edge, contact, current, destination) {
    const lift = await play(310, [
      [headline, [{ height: `${before}px` }, { height: `${after}px` }]],
      [actor, transform(current, pose(edge, contact + 8), destination)],
      ...[...arms].map((arm, i) => [arm, transform(
        i === 0 ? 'rotate(30deg)' : 'rotate(-33deg)',
        i === 0 ? 'rotate(-113deg)' : 'rotate(-122deg)',
        i === 0 ? 'rotate(30deg)' : 'rotate(-33deg)',
      )]),
    ]);
    if (!lift.length) return;
    headline.style.removeProperty('height');
    actor.style.transform = destination;
    lift.forEach(animation => animation.cancel());

    const run = await play(295, [
      [actor, transform(destination, pose(edge, contact * .4, -12), pose(edge, 8, 14), behindIcon)],
      ...gait(legs, -34), ...gait(arms, -38),
    ]);
    run.forEach(animation => animation.cancel());
    finish();
  }

  return async (next, kept, fullPhrase) => {
    if (typeof actor.animate !== 'function') return;
    if (closing) {
      if (!fullPhrase) return;
      await closing;
    }
    const currentWidth = headline.clientWidth;
    if (!actor.hidden && currentWidth !== width) finish();
    if (actor.hidden ? !fullPhrase : fullPhrase) return;
    if (!probe) {
      probe = kept.parentElement.cloneNode(true);
      probe.classList.add('ld-headline-probe');
      probeText = probe.querySelector('.typewriter-kept');
      headline.append(probe);
    }

    const before = headline.getBoundingClientRect().height;
    probeText.textContent = next;
    let after = probe.getBoundingClientRect().height;
    if (Math.abs(after - before) <= 1) return;
    const growing = after > before;
    if (!growing && (actor.hidden || after > startingHeight + 1)) return;
    if (growing && fullPhrase) {
      // Wait for a real wrap, then open enough room for the entire phrase.
      probeText.textContent = fullPhrase;
      after = Math.max(after, probe.getBoundingClientRect().height);
    }

    const style = getComputedStyle(actor);
    const edge = Number.parseFloat(style.getPropertyValue('--ld-helper-edge'));
    const scale = Number.parseFloat(style.getPropertyValue('--ld-helper-scale')) || 1;
    const contact = lead.offsetTop - 25 * scale;
    const current = pose(edge, contact);
    const destination = pose(edge, contact + after - before);
    width = currentWidth;

    if (!growing) {
      headline.style.height = `${before}px`;
      headline.classList.add('ld-helper-closing');
      closing = closeScene(before, after, edge, contact, current, destination)
        .finally(() => { closing = null; });
      return;
    }

    startingHeight = before;
    actor.hidden = false;
    actor.classList.remove('ld-helper-looking');
    const run = await play(350, [
      [actor, transform(behindIcon, pose(edge, 8, -14), pose(edge, contact * .55, 12), current)],
      ...gait(legs, 34), ...gait(arms, 38),
    ]);
    if (!run.length) return;
    actor.style.transform = current;
    run.forEach(animation => animation.cancel());

    headline.style.height = `${before}px`;
    const push = await play(275, [
      [headline, [{ height: `${before}px` }, { height: `${after}px` }]],
      [actor, transform(current, destination)],
      ...[...arms].map((arm, index) => [arm, transform(
        index === 0 ? 'rotate(-28deg)' : 'rotate(-35deg)',
        index === 0 ? 'rotate(-36deg) scaleY(1.17)' : 'rotate(-42deg) scaleY(1.22)',
        index === 0 ? 'rotate(-30deg)' : 'rotate(-36deg)',
      )]),
    ]);
    if (!push.length) return;
    kept.textContent = next;
    headline.style.height = `${after}px`;
    actor.style.transform = destination;
    actor.classList.add('ld-helper-looking');
    push.forEach(animation => animation.cancel());
  };
}
