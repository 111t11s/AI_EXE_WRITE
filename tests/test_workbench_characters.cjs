// Run with Node and Playwright installed. A system browser can be selected with
// AITXT_TEST_BROWSER_CHANNEL=msedge (or chrome).
const assert = require('node:assert/strict');
const {readFile} = require('node:fs/promises');
const path = require('node:path');
const {test, before, after} = require('node:test');
let chromium;
try { ({chromium} = require(process.env.PLAYWRIGHT_MODULE_PATH || 'playwright')); }
catch (error) { if (error.code !== 'MODULE_NOT_FOUND' || process.env.PLAYWRIGHT_MODULE_PATH) throw error; }
const browserTest = chromium ? test : test.skip;

let browser;
before(async () => {
  if (!chromium) return;
  browser = await chromium.launch({headless: true,
    ...(process.env.AITXT_TEST_BROWSER_CHANNEL ? {channel: process.env.AITXT_TEST_BROWSER_CHANNEL} : {})});
});
after(async () => { await browser?.close(); });

async function workbench(context, initialCharacters = []) {
  const page = await browser.newPage();
  context.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const project = {id: 1, title: '寻脉', genre: '仙侠', setting: '山海', tone: '热血', status: 'writing'};
  const volumes = [1, 2].map(number => ({id: number, outline_id: 1, volume_number: number,
    title: number === 1 ? '寻脉卷' : '远行卷', summary: '渡河寻脉'}));
  const chapter = {id: 1, project_id: 1, volume_id: 1, chapter_number: 1, title: '渡河',
    summary: '启程渡河', content: '', status: 'planned', word_count: 0};
  const character = {id: 7, name: '舟客', role: '配角 · 渡河向导', summary: '寻脉卷中帮助渡河'};
  const posts = [];
  const state = {characters: initialCharacters, chapter};
  const listing = items => ({items, has_more: false, limit: 100, offset: 0});
  await page.addInitScript(() => sessionStorage.setItem('mojian-session', 'test-session'));
  await page.route('http://workbench.test/**', async route => {
    const request = route.request();
    const url = new URL(request.url());
    const endpoint = url.pathname.replace('/api/v1', '');
    if (!url.pathname.startsWith('/api/v1/')) {
      const name = url.pathname === '/' ? 'index.html' : path.basename(url.pathname);
      const contentType = name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html';
      return route.fulfill({contentType, body: await readFile(path.join(__dirname, '../frontend', name), 'utf8')});
    }
    if (request.method() === 'POST') {
      posts.push({endpoint, data: request.postDataJSON()});
      if (endpoint.includes('/characters/core/')) state.characters = [character];
      return route.fulfill({status: 202, json: {id: String(posts.length), kind: endpoint.includes('/draft/') ? 'draft' : 'characters',
        project_id: 1, resource_id: 1, title: '测试生成任务', status: 'running', message: '生成中', created_at: '2026-11-15T12:00:00Z'}});
    }
    let result;
    if (endpoint === '/auth/me') result = {id: 1, username: 'writer', display_name: '作者'};
    else if (endpoint === '/projects') result = listing([project]);
    else if (endpoint === '/projects/1') result = project;
    else if (endpoint === '/generation-jobs') result = listing([]);
    else if (endpoint === '/worlds') result = listing([{id: 1, title: '山海', summary: '灵气衰竭', era: '古代', version_no: 1}]);
    else if (endpoint === '/outlines') result = listing([{id: 1, version_no: 1, summary: '寻脉', central_conflict: '宗门垄断'}]);
    else if (endpoint === '/outline-volumes') result = listing(volumes);
    else if (endpoint === '/plot-beats' || endpoint === '/volume-characters') result = listing([]);
    else if (endpoint === '/characters') result = listing(state.characters);
    else if (endpoint === '/chapters') result = listing([state.chapter]);
    else if (endpoint === '/chapters/1') result = state.chapter;
    else if (endpoint === '/writing/volumes/2/reusable-characters') result = {items: [{...character,
      previous_scenes: [{volume_id: 1, volume_number: 1, title: '寻脉卷', scenes: '帮助主角渡河'}]}]};
    else throw new Error(`Unexpected request: ${request.url()}`);
    return route.fulfill({json: result});
  });
  await page.goto('http://workbench.test/');
  await page.getByRole('button', {name: '章节与正文', exact: true}).waitFor();
  return {page, state, posts, errors};
}

browserTest('missing characters opens user settings before allowing a draft', async context => {
  const {page, posts, errors} = await workbench(context);
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  await page.getByRole('button', {name: '生成正文', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await page.getByRole('heading', {name: '生成主角与重要配角', exact: true}).waitFor();
  assert.equal(posts.length, 0);
  await dialog.getByLabel('新增主角数', {exact: true}).fill('2');
  await dialog.getByLabel('新增重要配角数', {exact: true}).fill('1');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.equal(posts[0].endpoint, '/writing/projects/1/characters/core/tasks');
  assert.equal(posts[0].data.protagonist_count, 2);
  assert.equal(posts[0].data.important_count, 1);
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  await page.getByRole('button', {name: '生成正文', exact: true}).click();
  await page.getByRole('heading', {name: '生成正文', exact: true}).waitFor();
  assert.equal(posts.length, 1);
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.equal(posts[1].endpoint, '/writing/chapters/1/draft/tasks');
  assert.deepEqual(errors, []);
});

browserTest('second volume submits checked old characters with zero new characters', async context => {
  const {page, posts, errors} = await workbench(context);
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '大纲与分卷', exact: true}).click();
  await page.getByRole('button', {name: '生成本卷配角', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await page.getByRole('heading', {name: '根据分卷大纲生成配角'}).waitFor();
  assert.equal(await dialog.locator('.reuse-characters').isVisible(), false);
  await dialog.getByLabel('目标分卷').selectOption('2');
  await dialog.getByRole('checkbox').check();
  assert.match(await dialog.locator('.reuse-characters').innerText(), /帮助主角渡河/);
  await dialog.getByLabel('新增配角数', {exact: true}).fill('0');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0].data, {volume_id: 2, supporting_count: 0, instruction: '', reuse_character_ids: [7]});
  assert.deepEqual(errors, []);
});

browserTest('changing back to volume one clears reused selections; long text scrolls inside reader', async context => {
  const {page, state, posts, errors} = await workbench(context, [{id: 7, name: '舟客', role: '配角', summary: '渡河'}]);
  await page.getByRole('button', {name: '角色', exact: true}).click();
  await page.getByRole('button', {name: '根据大纲补充配角', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByLabel('目标分卷').selectOption('2');
  await dialog.getByRole('checkbox').check();
  await dialog.getByLabel('目标分卷').selectOption('1');
  assert.equal(await dialog.getByRole('checkbox').count(), 0);
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0].data.reuse_character_ids, []);
  state.chapter = {...state.chapter, status: 'draft', content: '舟客将小船撑离河岸。\n'.repeat(1000), word_count: 10000};
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  const reader = page.getByRole('article', {name: '章节正文'});
  await reader.waitFor();
  assert.equal(await reader.evaluate(node => node.scrollHeight > node.clientHeight), true);
  await reader.evaluate(node => { node.scrollTop = node.scrollHeight; });
  await reader.scrollIntoViewIfNeeded();
  const before = await page.evaluate(() => window.scrollY);
  await reader.hover();
  await page.mouse.wheel(0, 1000);
  await page.waitForTimeout(250);
  assert.equal(await page.evaluate(() => window.scrollY), before);
  assert.deepEqual(errors, []);
});
