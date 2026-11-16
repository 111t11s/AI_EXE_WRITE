import assert from 'node:assert/strict';
import {test} from 'node:test';
import {authenticatedFetch, getAuthToken, setAuthToken} from '../frontend/api-client.js';
import {postGeneration, watchGeneration} from '../frontend/generation-stream.js';

function mockStorage(context) {
  const previous = {
    localStorage: Object.getOwnPropertyDescriptor(globalThis, 'localStorage'),
    sessionStorage: Object.getOwnPropertyDescriptor(globalThis, 'sessionStorage'),
  };
  const makeStorage = () => {
    const values = new Map();
    return {
      getItem: key => values.get(key) ?? null,
      setItem: (key, value) => values.set(key, String(value)),
      removeItem: key => values.delete(key),
      clear: () => values.clear(),
    };
  };
  for (const name of Object.keys(previous)) {
    Object.defineProperty(globalThis, name, {configurable: true, value: makeStorage()});
  }
  context.after(() => {
    for (const [name, descriptor] of Object.entries(previous)) {
      if (descriptor) Object.defineProperty(globalThis, name, descriptor);
      else delete globalThis[name];
    }
  });
}

test('login survives closing the tab and reopening the page', async (context) => {
  mockStorage(context);
  const firstPage = await import('../frontend/api-client.js?first-page');
  firstPage.setAuthToken('persistent-account-token');
  assert.equal(localStorage.getItem('mojian-session'), 'persistent-account-token');
  assert.equal(sessionStorage.getItem('mojian-session'), null);
  sessionStorage.clear();
  const reopenedPage = await import('../frontend/api-client.js?reopened-page');
  assert.equal(reopenedPage.getAuthToken(), 'persistent-account-token');
  reopenedPage.setAuthToken();
  const afterLogout = await import('../frontend/api-client.js?after-logout');
  assert.equal(afterLogout.getAuthToken(), '');
});

test('an existing tab session is migrated and cannot reappear after logout', async (context) => {
  mockStorage(context);
  sessionStorage.setItem('mojian-session', 'legacy-token');
  const page = await import('../frontend/api-client.js?legacy-page');
  assert.equal(page.getAuthToken(), 'legacy-token');
  assert.equal(localStorage.getItem('mojian-session'), 'legacy-token');
  assert.equal(sessionStorage.getItem('mojian-session'), null);
  page.setAuthToken();
  const reopenedPage = await import('../frontend/api-client.js?legacy-after-logout');
  assert.equal(reopenedPage.getAuthToken(), '');
});

test('a rejected persistent session is cleared before reopening the page', async (context) => {
  mockStorage(context);
  const previous = {fetch: globalThis.fetch, dispatchEvent: globalThis.dispatchEvent};
  context.after(() => Object.assign(globalThis, previous));
  const page = await import('../frontend/api-client.js?expired-persistent-page');
  page.setAuthToken('expired-persistent-token');
  globalThis.dispatchEvent = () => {};
  globalThis.fetch = async () => new Response('{}', {status: 401});
  await page.authenticatedFetch('/api/v1/auth/me');
  assert.equal(localStorage.getItem('mojian-session'), null);
  assert.equal(sessionStorage.getItem('mojian-session'), null);
  const reopenedPage = await import('../frontend/api-client.js?expired-reopened-page');
  assert.equal(reopenedPage.getAuthToken(), '');
});

test('blocked storage still allows login for the current page', async (context) => {
  mockStorage(context);
  for (const name of ['localStorage', 'sessionStorage']) {
    Object.defineProperty(globalThis, name, {configurable: true, get() { throw new Error('Storage blocked'); }});
  }
  const page = await import('../frontend/api-client.js?blocked-storage-page');
  assert.equal(page.getAuthToken(), '');
  page.setAuthToken('memory-only-token');
  assert.equal(page.getAuthToken(), 'memory-only-token');
  page.setAuthToken();
  assert.equal(page.getAuthToken(), '');
});

test('ordinary and streaming requests carry the account token in headers', async (context) => {
  const originalFetch = globalThis.fetch;
  context.after(() => { globalThis.fetch = originalFetch; setAuthToken(); });
  setAuthToken('test-account-token');
  const requests = [];
  globalThis.fetch = async (url, options) => {
    requests.push({url, ...options});
    if (url.endsWith('/stream')) return new Response('event: result\ndata: {"done":true}\n\n',
      {headers: {'content-type': 'text/event-stream'}});
    return new Response('{}');
  };
  await authenticatedFetch('/api/v1/projects');
  await postGeneration('/api/v1/writing/chapters/1/draft/stream', {}, () => {});
  assert.equal(requests.length, 2);
  assert.ok(requests.every(request => request.headers.get('Authorization') === 'Bearer test-account-token'));
  assert.ok(requests.every(request => !request.url.includes('test-account-token')));
});

test('expired sessions clear the token and notify the page to show login', async (context) => {
  const previous = {fetch: globalThis.fetch, dispatchEvent: globalThis.dispatchEvent};
  context.after(() => { Object.assign(globalThis, previous); setAuthToken(); });
  let notified = '';
  globalThis.dispatchEvent = (event) => { notified = event.type; };
  globalThis.fetch = async () => new Response('{}', {status: 401});
  setAuthToken('expired-token');
  assert.equal((await authenticatedFetch('/api/v1/projects')).status, 401);
  assert.equal(getAuthToken(), '');
  assert.equal(notified, 'aitxt:unauthorized');
});

test('a late failure from the previous session cannot log out a newer account', async (context) => {
  const previous = globalThis.fetch;
  context.after(() => { globalThis.fetch = previous; setAuthToken(); });
  let resolve;
  globalThis.fetch = () => new Promise(done => { resolve = done; });
  setAuthToken('old-token');
  const pending = authenticatedFetch('/api/v1/projects');
  setAuthToken('new-token');
  resolve(new Response('{}', {status: 401}));
  await pending;
  assert.equal(getAuthToken(), 'new-token');
});

test('opening progress uses an authenticated cancellable GET without starting generation', async (context) => {
  const previous = globalThis.fetch;
  context.after(() => { globalThis.fetch = previous; setAuthToken(); });
  setAuthToken('progress-account-token');
  const controller = new AbortController();
  const events = [];
  globalThis.fetch = async (url, options) => {
    assert.equal(url, '/api/v1/generation-jobs/job-id/events');
    assert.equal(options.method, 'GET');
    assert.equal(options.body, undefined);
    assert.equal(options.signal, controller.signal);
    assert.equal(options.headers.get('Authorization'), 'Bearer progress-account-token');
    return new Response('event: snapshot\ndata: {"text":"已生成"}\n\nevent: result\ndata: {"saved":true}\n\n',
      {headers: {'Content-Type': 'text/event-stream'}});
  };
  const result = await watchGeneration('/api/v1/generation-jobs/job-id/events',
    (event, data) => events.push([event, data]), {signal: controller.signal});
  assert.deepEqual(result, {saved: true});
  assert.deepEqual(events[0], ['snapshot', {text: '已生成'}]);
});
