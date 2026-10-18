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
test('Previously unreachable region is unavailable when both APIs fail', () => assert.equal(availability(region(false,false,{web_state:'blocked'}),1010).state,'down'));
test('Altai web remains available', () => assert.equal(availability(region(true,false),1010).label,'Веб работает'));
