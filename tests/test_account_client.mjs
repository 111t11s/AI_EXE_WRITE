import assert from 'node:assert/strict';
import {test} from 'node:test';
import {authenticatedFetch, getAuthToken, setAuthToken} from '../frontend/api-client.js';
import {postGeneration} from '../frontend/generation-stream.js';

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
