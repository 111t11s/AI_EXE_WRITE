import {cp, mkdir, readFile, writeFile} from 'node:fs/promises';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const mobileRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const frontendRoot = path.resolve(mobileRoot, '../frontend');
const targetRoot = path.join(mobileRoot, 'www');
const configured = process.env.AITXT_PUBLIC_API_URL || 'http://49.232.132.111/api/v1';
const url = new URL(configured);
if (!['https:', 'http:'].includes(url.protocol) || url.username || url.password || url.search || url.hash) {
  throw new Error('AITXT_PUBLIC_API_URL must be HTTP(S) without credentials, query or hash.');
}
if (url.protocol === 'http:' && url.hostname !== '49.232.132.111') {
  throw new Error('HTTP is only allowed for the configured private-group server 49.232.132.111.');
}
if (['localhost', '127.0.0.1', '[::1]', '0.0.0.0'].includes(url.hostname)) {
  throw new Error('Android must use a server reachable from the phone, not loopback.');
}
if (url.pathname.replace(/\/+$/, '') !== '/api/v1') {
  throw new Error('AITXT_PUBLIC_API_URL must end in /api/v1.');
}

await mkdir(targetRoot, {recursive: true});
for (const file of ['index.html', 'workbench.css', 'workbench.js', 'api-client.js']) {
  await cp(path.join(frontendRoot, file), path.join(targetRoot, file));
}
// Only the public URL goes into the APK, never the server's .env.
await writeFile(path.join(targetRoot, 'runtime-config.js'),
  'window.AITXT_CONFIG = ' + JSON.stringify({apiBaseUrl: url.href.replace(/\/+$/, '')}) + ';\n');
let html = await readFile(path.join(targetRoot, 'index.html'), 'utf8');
html = html.replace('content="width=device-width,initial-scale=1"',
  'content="width=device-width,initial-scale=1,viewport-fit=cover"');
html = html.replace(/<a href="\/docs"[^>]*>接口文档 ↗<\/a>/, '<span>墨间 · 创作随行</span>');
await writeFile(path.join(targetRoot, 'index.html'), html);
console.log('Android web assets ready. Public API:', url.href);
