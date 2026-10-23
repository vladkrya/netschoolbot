import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
const code = readFileSync(new URL('../../app/web/static/status/availability.js', import.meta.url), 'utf8');
const { availability } = await import(`data:text/javascript,${encodeURIComponent(code)}`);
const region = (web, mobile, extra = {}) => ({ in_app: true, latest_check: { checked_at: 1000, ok: web, mobile }, ...extra });
for (const [web, mobile, expected] of [[true,true,'up'],[true,false,'up'],[false,true,'up'],[false,false,'down'],[false,null,'unknown']]) {
  test(`web=${web}, mobile=${mobile}: ${expected}`, () => assert.equal(availability(region(web,mobile),1010).state,expected));
}
test('No mobile catalogue: web determines availability', () => assert.equal(availability(region(false,null,{in_app:false}),1010).state,'down'));
test('Stale checks do not become an outage', () => assert.equal(availability(region(false,false),1200).state,'unknown'));
test('Region never reachable from our servers is not an outage', () => assert.equal(availability(region(false,false,{state:'blocked'}),1010).label,'Недоступен с наших серверов'));
test('Blocked region with working mobile API is up', () => assert.equal(availability(region(false,true,{state:'blocked'}),1010).state,'up'));
test('Altai web remains available', () => assert.equal(availability(region(true,false),1010).label,'Веб работает'));
test('Diaries failing while site answers is not green', () => {
  const state = availability(region(true, true, { diary_issues: true }), 1010);
  assert.equal(state.state, 'issues');
  assert.equal(state.tone, 'warning');
  assert.equal(state.label, 'Проблемы с дневниками');
});
test('Full outage outranks diary issues', () => assert.equal(availability(region(false,false,{diary_issues:true}),1010).state,'down'));
test('Diary issues ignored once checks are stale', () => assert.equal(availability(region(true,true,{diary_issues:true}),1200).state,'unknown'));
