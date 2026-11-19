import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

const profile = readFileSync(new URL('../../app/web/static/js/profile.js', import.meta.url), 'utf8');
const binding = profile.slice(profile.indexOf('function bindToolGroups('), profile.indexOf('// Заказ справок'));

for (const [group, toolId] of [['security', 'drawerLock'], ['tools', 'drawerStatus']]) {
  test(`${group} opens one destination per sheet`, () => {
    let start;
    let grid;
    let visible = false;
    let opened = 0;
    const panel = { querySelector: () => grid };
    const button = {
      dataset: { toolGroup: group },
      querySelector: () => ({ textContent: group }),
      addEventListener: (_, handler) => { start = handler; },
    };
    const tool = {
      id: toolId,
      matches: () => true,
      cloneNode: () => ({
        dataset: {},
        removeAttribute() {},
        addEventListener(_, handler) { this.activate = handler; },
      }),
    };
    const body = {
      querySelectorAll: () => [button],
      querySelector: () => ({ querySelectorAll: () => [tool, { ...tool, id: 'anotherTool' }] }),
    };
    const bind = vm.runInNewContext(`${binding}\nbindToolGroups`, {
      ONLINE_ONLY: '#drawerLock, #drawerStatus',
      tap() {},
      openSheet() {
        if (grid) grid.parentElement = null;
        grid = { parentElement: panel, tiles: [], append(tile) { this.tiles.push(tile); } };
        visible = true;
        return panel;
      },
      closeSheet() { visible = false; },
      sheetOpen: () => visible,
    });
    bind(body, new Map([
      [toolId, () => { assert.equal(visible, false); opened++; }],
      ['anotherTool', () => assert.fail('A second tile opened a destination')],
    ]));

    start();
    const [tile, another] = grid.tiles;
    assert.equal(tile.dataset.online, '');
    tile.activate();
    tile.activate();
    another.activate();
    assert.equal(opened, 1);

    start();
    tile.activate();
    grid.tiles[0].activate();
    assert.equal(opened, 2);
  });
}
