import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';

const source = await readFile(new URL('../frontend/api-client.js', import.meta.url), 'utf8');
const {resolveApiBaseUrl} = await import('data:text/javascript;base64,' + Buffer.from(source).toString('base64'));
assert.equal(resolveApiBaseUrl(), '/api/v1');
assert.equal(resolveApiBaseUrl('http://49.232.132.111/api/v1/'), 'http://49.232.132.111/api/v1');
assert.equal(resolveApiBaseUrl('https://novel.example.com/api/v1'), 'https://novel.example.com/api/v1');
for (const invalid of ['javascript:alert(1)', 'https://user:password@example.com/api/v1', 'https://example.com/api/v1?key=secret']) {
  assert.throws(() => resolveApiBaseUrl(invalid));
}
console.log('Client API URL checks passed.');
