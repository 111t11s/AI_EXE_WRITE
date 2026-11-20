// Run with Node and Playwright; AITXT_TEST_BROWSER_CHANNEL=msedge supports system Edge.
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
  if (chromium) browser = await chromium.launch({headless: true,
    ...(process.env.AITXT_TEST_BROWSER_CHANNEL ? {channel: process.env.AITXT_TEST_BROWSER_CHANNEL} : {})});
});
after(async () => { await browser?.close(); });

async function library(context, options = {}) {
  const page = await browser.newPage({viewport: options.viewport || {width: 1280, height: 900}});
  context.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const chapter = {id: 1, chapter_number: 1, title: '渡河', content: '少年踏上渡船，驶向山海。\n'.repeat(200),
    word_count: 2400, updated_at: '2026-11-20T12:00:00'};
  const book = {id: 1, title: '寻脉', author: '林间客', genre: '仙侠', status: 'writing',
    premise: '灵气渐衰，一位少年踏上寻回山海灵脉的旅途。', chapter_count: 1, updated_at: '2026-11-20T12:00:00'};
  const state = {books: options.books || [book], chapters: options.chapters || [chapter], requests: [], available: true, fail: false};
  const listing = (items, url) => {
    const limit = Number(url.searchParams.get('limit') || 100);
    const offset = Number(url.searchParams.get('offset') || 0);
    return {items: items.slice(offset, offset + limit), limit, offset, has_more: offset + limit < items.length};
  };
  await page.route('http://library.test/**', async route => {
    const url = new URL(route.request().url());
    if (!url.pathname.startsWith('/api/v1/')) {
      const name = path.basename(url.pathname) || 'public.html';
      return route.fulfill({contentType: name.endsWith('.js') ? 'text/javascript' : name.endsWith('.css') ? 'text/css' : 'text/html',
        body: await readFile(path.join(__dirname, '../frontend', name), 'utf8')});
    }
    assert.equal(route.request().headers().authorization, undefined);
    state.requests.push(url.pathname + url.search);
    if (state.fail) return route.fulfill({status: 503, json: {detail: '数据库暂时不可用'}});
    if (url.pathname === '/api/v1/public/projects') return route.fulfill({json: listing(state.available ? state.books : [], url)});
    const match = url.pathname.match(/^\/api\/v1\/public\/projects\/(\d+)(?:\/chapters(?:\/(\d+))?)?$/);
    const current = state.books.find(row => row.id === Number(match?.[1]));
    if (!state.available || !current) return route.fulfill({status: 404, json: {detail: '书籍不存在或尚未发布'}});
    if (url.pathname.endsWith('/chapters')) {
      return route.fulfill({json: listing(state.chapters.map(({content, ...metadata}) => metadata), url)});
    }
    return route.fulfill({json: match[2] ? state.chapters.find(row => row.id === Number(match[2])) : current});
  });
  await page.clock.install();
  await page.goto(`http://library.test/public.html${options.query || ''}`);
  await page.waitForFunction(() => document.getElementById('public-view').getAttribute('aria-busy') === 'false');
  return {page, state, errors};
}

browserTest('visitors browse and read, navigate chapters and keep reading position during automatic updates', async context => {
  const {page, state, errors} = await library(context);
  await page.getByRole('link', {name: '阅读书籍', exact: true}).click();
  await page.getByLabel('章节正文', {exact: true}).waitFor();
  assert.match(await page.getByLabel('章节正文', {exact: true}).innerText(), /少年踏上渡船/);
  await page.getByLabel('章节正文', {exact: true}).evaluate(element => { element.scrollTop = 180; });
  state.chapters[0].content = '作者修改后的最新正文。\n'.repeat(200);
  state.chapters.push({...state.chapters[0], id: 2, chapter_number: 2, title: '山海', content: '新章节正文'});
  state.books[0].chapter_count = 2;
  await page.clock.fastForward(30001);
  await page.waitForFunction(() => document.querySelector('.public-reader')?.textContent.startsWith('作者修改后'));
  assert.equal(await page.getByLabel('章节正文', {exact: true}).evaluate(element => element.scrollTop), 180);
  assert.equal(await page.getByRole('navigation', {name: '章节目录', exact: true}).getByRole('link').count(), 2);
  await page.getByRole('link', {name: '下一章 →', exact: true}).click();
  await page.getByRole('heading', {name: '第 2 章 · 山海', exact: true}).waitFor();
  assert.equal(await page.getByLabel('章节正文', {exact: true}).innerText(), '新章节正文');
  await page.getByRole('link', {name: '← 上一章', exact: true}).click();
  await page.getByRole('heading', {name: '第 1 章 · 渡河', exact: true}).waitFor();
  state.available = false;
  await page.getByRole('button', {name: '刷新内容', exact: true}).click();
  await page.getByRole('heading', {name: '书籍或章节已不可用', exact: true}).waitFor();
  assert.equal(await page.getByLabel('章节正文', {exact: true}).count(), 0);
  assert.deepEqual(errors, []);
});

browserTest('public shelf paginates and reader loads more than one page of chapter metadata', async context => {
  const books = Array.from({length: 21}, (_, index) => ({id: index + 1, title: `小说 ${index + 1}`, genre: '仙侠',
    author: '作者', premise: '', status: 'writing', chapter_count: 0, updated_at: '2026-11-20T12:00:00'}));
  const {page, state, errors} = await library(context, {books});
  assert.equal(await page.getByRole('link', {name: '阅读书籍', exact: true}).count(), 20);
  await page.getByRole('link', {name: '下一页', exact: true}).click();
  await page.getByText('第 2 页', {exact: true}).waitFor();
  assert.equal(await page.getByRole('link', {name: '阅读书籍', exact: true}).count(), 1);
  state.chapters = Array.from({length: 101}, (_, index) => ({...state.chapters[0], id: index + 1, chapter_number: index + 1}));
  await page.getByRole('link', {name: '阅读书籍', exact: true}).click();
  await page.getByLabel('章节正文', {exact: true}).waitFor();
  assert.equal(await page.getByRole('navigation', {name: '章节目录', exact: true}).getByRole('link').count(), 101);
  assert.ok(state.requests.some(request => request.includes('/21/chapters?limit=100&offset=100')));
  assert.deepEqual(errors, []);
});

browserTest('mobile reader renders text safely and recovers after a refresh failure', async context => {
  const {page, state, errors} = await library(context, {query: '?book=1', viewport: {width: 390, height: 844}});
  state.chapters[0].content = '<img src=x onerror="window.injected=true">';
  await page.getByRole('button', {name: '刷新内容', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('.public-reader')?.textContent.startsWith('<img'));
  assert.equal(await page.evaluate(() => window.injected), undefined);
  assert.equal(await page.locator('.public-reader img').count(), 0);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth));
  state.fail = true;
  await page.getByRole('button', {name: '刷新内容', exact: true}).click();
  await page.getByRole('status').getByText('数据库暂时不可用', {exact: true}).waitFor();
  assert.equal(await page.getByLabel('章节正文', {exact: true}).count(), 1);
  state.fail = false;
  state.chapters[0].content = '连接恢复后的正文';
  await page.getByRole('button', {name: '刷新内容', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('.public-reader')?.textContent === '连接恢复后的正文');
  assert.deepEqual(errors, []);
});

function manyChapters() {
  return Array.from({length: 80}, (_, index) => ({id: index + 1, chapter_number: index + 1,
    title: `山海行记 ${index + 1}`, content: `第 ${index + 1} 章的故事。\n\n`.repeat(240),
    word_count: 3600, updated_at: '2026-11-20T12:00:00'}));
}

browserTest('chapter changes keep the reader frame and directory position with independent scrolling', async context => {
  const {page, state, errors} = await library(context, {query: '?book=1', chapters: manyChapters()});
  const body = page.getByLabel('章节正文', {exact: true});
  const directory = page.getByRole('navigation', {name: '章节目录', exact: true});
  const panel = page.locator('.public-reader-panel');
  const footer = page.getByRole('navigation', {name: '章节翻页'});
  await body.evaluate(element => { element.scrollTop = 350; window.originalReader = element; });
  await directory.getByRole('link').nth(12).scrollIntoViewIfNeeded();
  const scroll = await directory.evaluate(element => element.scrollTop);
  assert.ok(scroll > 0);
  const frame = await panel.boundingBox();
  const footerFrame = await footer.boundingBox();
  await directory.getByRole('link').nth(12).click();
  await page.getByRole('heading', {name: '第 13 章 · 山海行记 13', exact: true}).waitFor();
  assert.equal(await body.evaluate(element => element === window.originalReader), true);
  assert.equal(await body.evaluate(element => element.scrollTop), 0);
  assert.equal(await directory.evaluate(element => element.scrollTop), scroll);
  assert.deepEqual(await panel.boundingBox(), frame);
  assert.deepEqual(await footer.boundingBox(), footerFrame);
  assert.equal(await directory.locator('[aria-current=page]').innerText(), '第 13 章 · 山海行记 13');
  await body.hover();
  await page.mouse.wheel(0, 600);
  await page.waitForFunction(() => document.querySelector('.public-reader').scrollTop > 0);
  assert.deepEqual(await footer.boundingBox(), footerFrame);
  assert.equal(await page.evaluate(() => window.scrollY), 0);
  assert.ok(await page.evaluate(() => document.documentElement.scrollHeight <= window.innerHeight));
  const bodyScroll = await body.evaluate(element => element.scrollTop);
  state.chapters[12].content += '\n作者新增的一段。';
  await page.getByRole('button', {name: '刷新内容', exact: true}).click();
  await page.waitForFunction(() => document.querySelector('.public-reader').textContent.endsWith('作者新增的一段。'));
  assert.equal(await body.evaluate(element => element.scrollTop), bodyScroll);
  await page.getByRole('link', {name: '下一章 →', exact: true}).click();
  await page.getByRole('heading', {name: '第 14 章 · 山海行记 14', exact: true}).waitFor();
  await page.goBack();
  await page.getByRole('heading', {name: '第 13 章 · 山海行记 13', exact: true}).waitFor();
  assert.deepEqual(await panel.boundingBox(), frame);
  assert.equal(await directory.evaluate(element => element.scrollTop), scroll);
  assert.equal(await body.evaluate(element => element.scrollTop), 0);
  await page.goForward();
  await page.getByRole('heading', {name: '第 14 章 · 山海行记 14', exact: true}).waitFor();
  assert.equal(await body.evaluate(element => element === window.originalReader), true);
  assert.deepEqual(errors, []);
});

for (const viewport of [{width: 390, height: 844}, {width: 360, height: 640}, {width: 740, height: 390}]) {
  browserTest(`mobile directory overlays a fixed reader at ${viewport.width}x${viewport.height}`, async context => {
    const {page, errors} = await library(context, {query: '?book=1', viewport, chapters: manyChapters()});
    const body = page.getByLabel('章节正文', {exact: true});
    const directory = page.getByRole('navigation', {name: '章节目录', exact: true});
    const toggle = page.getByRole('button', {name: '目录', exact: true});
    const frame = await page.locator('.public-reader-panel').boundingBox();
    assert.equal(await directory.isVisible(), false);
    assert.ok((await body.boundingBox()).height >= (viewport.height > 600 ? 300 : 100));
    await body.evaluate(element => { element.scrollTop = 240; });
    await toggle.click();
    assert.equal(await toggle.getAttribute('aria-expanded'), 'true');
    await page.keyboard.press('Shift+Tab');
    assert.equal(await directory.getByRole('link').last().evaluate(element => element === document.activeElement), true);
    await page.keyboard.press('Tab');
    assert.equal(await page.getByRole('button', {name: '收起目录'}).evaluate(element => element === document.activeElement), true);
    await directory.getByRole('link').nth(20).click();
    await page.getByRole('heading', {name: '第 21 章 · 山海行记 21', exact: true}).waitFor();
    assert.equal(await directory.isVisible(), false);
    assert.equal(await toggle.getAttribute('aria-expanded'), 'false');
    assert.equal(await body.evaluate(element => element.scrollTop), 0);
    assert.deepEqual(await page.locator('.public-reader-panel').boundingBox(), frame);
    await toggle.click();
    await directory.getByRole('link').nth(20).click();
    await page.waitForFunction(() => !document.querySelector('.public-reading').classList.contains('is-directory-open'));
    assert.equal(await body.evaluate(element => element.scrollTop), 0);
    await toggle.click();
    await page.keyboard.press('Escape');
    assert.equal(await directory.isVisible(), false);
    assert.equal(await toggle.evaluate(element => element === document.activeElement), true);
    assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth && document.documentElement.scrollHeight <= innerHeight));
    await page.getByRole('link', {name: '← 返回公共书架', exact: true}).click();
    await page.getByRole('heading', {name: '公共书架', exact: true}).waitFor();
    assert.equal(await page.locator('.public-workspace').evaluate(element => element.classList.contains('is-reading')), false);
    assert.deepEqual(errors, []);
  });
}
