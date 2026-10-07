import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { test } from 'node:test';
import assert from 'node:assert/strict';

const script = readFileSync(new URL('../index.html', import.meta.url), 'utf8').match(/<script>([\s\S]*?)<\/script>/)[1];
function harness({ token = '', query = '', fetchImpl } = {}) {
  const timers = new Map(); let timerId = 0;
  const element = () => ({value: '', children: [], disabled: false, handlers: {}, textContent: '',
    addEventListener(kind, fn) {this.handlers[kind] = fn;}, appendChild(child) {this.children.push(child);},
    insertBefore(child) {this.children.unshift(child);}, removeChild(child) {this.children.splice(this.children.indexOf(child), 1);},
    get firstChild() {return this.children[0];}, get lastChild() {return this.children.at(-1);}, querySelector() {return null;}});
  const elements = Object.fromEntries(['base','token','connect','disconnect','status','statusText','fleet','components','log','eventCounts'].map(id => [id, element()]));
  elements.base.value = 'http://localhost:9889'; elements.token.value = token;
  const sources = [], requests = [];
  class Source {
    constructor(url) {this.url = url; this.listeners = {}; sources.push(this);}
    addEventListener(kind, fn) {this.listeners[kind] = fn;}
    close() {this.closed = true;}
  }
  const location = {href: `http://localhost:8000/viewer${query}`, pathname: '/viewer', search: query, hash: ''};
  const scope = {URL, AbortController, EventSource: Source, location, addEventListener() {},
    history: {replaceState() {}},
    document: {getElementById: id => elements[id], createElement: element, createTextNode: text => ({textContent: text})},
    fetch: async (url, options) => {requests.push({url, options}); return fetchImpl ? fetchImpl(url, options, requests.length) : new Response(JSON.stringify({ticket: `single-${requests.length}`}));},
    setTimeout: (fn, ms) => {const id = ++timerId; timers.set(id, {fn, ms}); return id;}, clearTimeout: id => timers.delete(id)};
  scope.window = scope; vm.runInNewContext(script, scope);
  return {scope, elements, sources, requests, advance: async ms => {const due = [...timers].filter(([, t]) => t.ms <= ms); for (const [id, t] of due) {timers.delete(id); t.fn();} await flush();}};
}
async function flush() {for (let i = 0; i < 30; i++) await Promise.resolve();}

test('ticket mint keeps bearer in header and reconnect preserves cursor with a fresh ticket', async () => {
  const h = harness({token: 'operator.reusable.token'}); await flush();
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].options.method, 'POST');
  assert.equal(h.requests[0].options.headers.Authorization, 'Bearer operator.reusable.token');
  assert.equal(h.requests[0].options.redirect, 'error');
  assert.equal(new URL(h.sources[0].url).searchParams.get('ticket'), 'single-1');
  assert.ok(!h.sources[0].url.includes('operator.reusable.token'));
  h.sources[0].listeners.RUN_STARTED({data: '{"run_id":"one"}', lastEventId: 'confirmed'});
  h.sources[0].onerror(); assert.equal(h.sources[0].closed, true);
  await h.advance(1000);
  assert.equal(new URL(h.requests[1].url).searchParams.get('cursor'), 'confirmed');
  assert.equal(new URL(h.sources[1].url).searchParams.get('cursor'), 'confirmed');
  assert.equal(new URL(h.sources[1].url).searchParams.get('ticket'), 'single-2');
  h.elements.disconnect.handlers.click();
});
test('URL query credentials never seed the token input', async () => {
  const h = harness({query: '?access_token=leaked.master&token=other'}); await flush();
  assert.equal(h.elements.token.value, '');
  assert.equal(h.requests[0].options.headers.Authorization, undefined);
  assert.ok(!h.sources[0].url.includes('leaked.master'));
  h.elements.disconnect.handlers.click();
});
test('a stalled mint reaches a visible deadline and disconnect stops retries', async () => {
  const h = harness({fetchImpl: () => new Promise(() => {})}); await flush();
  await h.advance(10000);
  assert.equal(h.requests.length, 1);
  assert.equal(h.requests[0].options.signal.aborted, true);
  assert.match(h.elements.statusText.textContent, /unavailable|timed out/i);
  h.elements.disconnect.handlers.click();
  await h.advance(10000);
  assert.equal(h.requests.length, 1);
});

test('expired cursors visibly resync retained state before opening a fresh stream', async () => {
  const h = harness({fetchImpl: (_url, _options, count) => count === 2 ? new Response('{}', {status: 409}) : new Response(JSON.stringify({ticket: `single-${count}`}))});
  await flush();
  h.sources[0].listeners.RUN_STARTED({data: '{}', lastEventId: 'expired'});
  h.sources[0].onerror(); await h.advance(1000);
  assert.match(h.elements.statusText.textContent, /History cursor expired/);
  assert.equal(new URL(h.requests[1].url).searchParams.get('cursor'), 'expired');
  await h.advance(1000);
  assert.equal(new URL(h.requests[2].url).searchParams.has('cursor'), false);
  assert.equal(h.sources.length, 2);
  h.elements.disconnect.handlers.click();
});
test('the mint body uses the same deadline and late completion cannot create a stream after disconnect', async () => {
  let resolve;
  const h = harness({fetchImpl: () => Promise.resolve({ok: true, json: () => new Promise(r => {resolve = r;})})});
  await flush(); await h.advance(10000);
  assert.equal(h.requests[0].options.signal.aborted, true);
  assert.match(h.elements.statusText.textContent, /timed out/);
  h.elements.disconnect.handlers.click(); resolve({ticket: 'too-late'}); await flush();
  assert.equal(h.sources.length, 0);
  assert.equal(h.elements.statusText.textContent, 'disconnected');
});

test('named cursor_expired closes the consumed source and explicitly resyncs without waiting for error', async () => {
  const h = harness(); await flush();
  h.sources[0].listeners.RUN_STARTED({data: '{}', lastEventId: 'expired'});
  assert.equal(typeof h.sources[0].listeners.cursor_expired, 'function');
  h.sources[0].listeners.cursor_expired({data: '{"code":"event_cursor_expired","resync_required":true}'});
  h.sources[0].onerror(); // Following EOF cannot duplicate the reconnect.
  assert.equal(h.sources[0].closed, true);
  assert.match(h.elements.statusText.textContent, /History cursor expired/);
  await h.advance(1000);
  assert.equal(h.requests.length, 2);
  assert.equal(new URL(h.requests[1].url).searchParams.has('cursor'), false);
  assert.equal(new URL(h.sources[1].url).searchParams.has('cursor'), false);
  assert.equal(new URL(h.sources[1].url).searchParams.get('ticket'), 'single-2');
  h.elements.disconnect.handlers.click();
});
