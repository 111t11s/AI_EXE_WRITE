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

async function workbench(context, initialCharacters = [], options = {}) {
  const page = await browser.newPage();
  context.after(() => page.close());
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  const project = {id: 1, title: '寻脉', genre: '仙侠', setting: '山海', tone: '热血', status: 'writing', ...options.project};
  const volumes = [1, 2].map(number => ({id: number, outline_id: 1, volume_number: number,
    title: number === 1 ? '寻脉卷' : '远行卷', summary: '渡河寻脉'}));
  const chapter = {id: 1, project_id: 1, volume_id: 1, chapter_number: 1, title: '渡河',
    summary: '启程渡河', content: '', status: 'planned', word_count: 0};
  const character = {id: 7, name: '舟客', role: '配角 · 渡河向导', summary: '寻脉卷中帮助渡河'};
  const posts = [];
  const state = {characters: initialCharacters, chapter, exports: [], jobs: options.jobs || [], profiles: [], characterProgress: [],
    projects: options.projects || [project]};
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
    if (endpoint === '/projects/1/export') {
      state.exports.push({params: request.method() === 'POST' ? request.postDataJSON() : Object.fromEntries(url.searchParams), authorization: request.headers().authorization});
      if (state.exportError) return route.fulfill({status: 409, json: {detail: state.exportError}});
      return route.fulfill({headers: {'content-type': 'application/zip',
        'content-disposition': `attachment; filename="novel-1.zip"; filename*=UTF-8''${encodeURIComponent('寻脉.zip')}`},
        body: Buffer.from('UEsDBBQAAAgIABiicF1TsVk0GgAAABUAAAAWAAAA56ysMDAwMDHnq6Bf5rih5rKzLnR4dAEVAOr/6Iif5a6i5pKR6Ii55rih5rKz44CCUEsBAhQAFAAACAgAGKJwXVOxWTQaAAAAFQAAABYAAAAAAAAAAAAAAIABAAAAAOesrDAwMDAx56ugX+a4oeaysy50eHRQSwUGAAAAAAEAAQBEAAAATgAAAAAA', 'base64')});
    }
    if (request.method() === 'PATCH' && endpoint === '/projects/1') {
      const data = request.postDataJSON();
      posts.push({endpoint, data, method: 'PATCH'});
      Object.assign(state.projects.find(item => item.id === 1), data);
      return route.fulfill({json: state.projects.find(item => item.id === 1)});
    }
    if (request.method() === 'PATCH' && endpoint === '/writing/projects/1/consistency/consistency/issues/0') {
      const data = request.postDataJSON();
      posts.push({endpoint, data, method: 'PATCH'});
      Object.assign(state.consistencyReport.issues[0], data);
      return route.fulfill({json: state.consistencyReport.issues[0]});
    }
    if (request.method() === 'POST') {
      posts.push({endpoint, data: request.postDataJSON()});
      if (endpoint.endsWith('/issues/0/repair/tasks')) {
        Object.assign(state.consistencyReport.issues[0], request.postDataJSON(), {repair_job_id: 'repair', repair_status: 'running'});
        const job = {id: 'repair', kind: 'consistency_repair', project_id: 1, resource_id: 1,
          title: '一致性修复 · 人物关系', status: 'running', message: '修复中', created_at: '2026-11-19T12:00:00Z'};
        state.jobs.push(job);
        return route.fulfill({status: 202, json: job});
      }
      if (endpoint.includes('/characters/core/')) state.characters = [character];
      return route.fulfill({status: 202, json: {id: String(posts.length), kind: endpoint.includes('/chapters/') ? 'draft' :
        endpoint.includes('/rewrite/') ? 'outline' : 'characters',
        project_id: 1, resource_id: 1, title: '测试生成任务', status: 'running', message: '生成中', created_at: '2026-11-15T12:00:00Z'}});
    }
    let result;
    if (endpoint === '/auth/me') result = {id: 1, username: 'writer', display_name: '作者', ...options.account};
    else if (endpoint === '/projects') result = listing(state.projects);
    else if (/^\/projects\/\d+$/.test(endpoint)) result = state.projects.find(row => row.id === Number(endpoint.split('/').pop()));
    else if (endpoint === '/generation-jobs') result = listing(state.jobs);
    else if (endpoint === '/generation-jobs/consistency/events') return route.fulfill({
      contentType: 'text/event-stream', body: `event: snapshot\ndata: ${JSON.stringify(state.jobs[0])}\n\nevent: result\ndata: ${JSON.stringify(state.consistencyReport)}\n\n`,
    });
    else if (endpoint === '/generation-jobs/repair/events') return route.fulfill({
      contentType: 'text/event-stream', body: `event: snapshot\ndata: ${JSON.stringify(state.jobs.find(job => job.id === 'repair'))}\n\nevent: result\ndata: ${JSON.stringify(state.repairResult)}\n\n`,
    });
    else if (endpoint === '/worlds') result = listing([{id: 1, title: '山海', summary: '灵气衰竭', era: '古代', version_no: 1}]);
    else if (endpoint === '/novel-assistant/outline-skills') result = {genre: '仙侠', specialized: true,
      skills: [{id: 'outline-general', name: '通用小说大纲设计技能'}], notice: '已加载：通用小说大纲设计技能'};
    else if (endpoint === '/outlines') result = listing([{id: 1, version_no: 1, summary: '寻脉', central_conflict: '宗门垄断'}]);
    else if (endpoint === '/outline-volumes') result = listing(volumes);
    else if (endpoint === '/plot-beats' || endpoint === '/volume-characters') result = listing([]);
    else if (endpoint === '/characters') result = listing(state.characters);
    else if (endpoint === '/writing/projects/1/character-progress') {
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 100);
      result = {items: state.characterProgress.slice(offset, offset + limit), offset, limit,
        has_more: offset + limit < state.characterProgress.length};
    }
    else if (endpoint === '/writing/projects/1/character-profiles') result = {outline_id: 1, outline_version: 1, items: state.profiles};
    else if (endpoint === '/chapters') {
      const rows = state.chapters || [state.chapter];
      const offset = Number(url.searchParams.get('offset') || 0);
      const limit = Number(url.searchParams.get('limit') || 100);
      result = {items: rows.slice(offset, offset + limit), offset, limit, has_more: offset + limit < rows.length};
    }
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

browserTest('authors can publish and unpublish their book from the overview', async context => {
  const {page, posts, errors} = await workbench(context);
  page.on('dialog', dialog => dialog.accept());
  await page.getByRole('button', {name: '发布书籍', exact: true}).click();
  await page.getByRole('button', {name: '取消发布', exact: true}).waitFor();
  assert.equal(posts.at(-1).endpoint, '/projects/1');
  assert.deepEqual(posts.at(-1).data, {is_public: true});
  assert.equal(await page.getByRole('link', {name: '公开阅读 ↗', exact: true}).getAttribute('href'), '/public.html?book=1');
  await page.getByRole('button', {name: '取消发布', exact: true}).click();
  await page.getByRole('button', {name: '发布书籍', exact: true}).waitFor();
  assert.deepEqual(posts.at(-1).data, {is_public: false});
  assert.equal(await page.getByRole('link', {name: '公开阅读 ↗', exact: true}).count(), 0);
  assert.deepEqual(errors, []);
});

browserTest('administrators viewing another authors book have no publication controls', async context => {
  const {page, errors} = await workbench(context, [], {account: {is_admin: true}, project: {account_id: 2, is_public: true}});
  assert.equal(await page.getByRole('button', {name: '发布书籍', exact: true}).count(), 0);
  assert.equal(await page.getByRole('button', {name: '取消发布', exact: true}).count(), 0);
  assert.equal(await page.getByRole('link', {name: '公开阅读 ↗', exact: true}).count(), 1);
  assert.deepEqual(errors, []);
});

function taskJobs(count) {
  return Array.from({length: count}, (_, index) => ({id: `task-${index + 1}`, project_id: 1,
    kind: 'outline', status: 'completed', title: `大纲任务 ${index + 1}`, message: '已生成',
    created_at: new Date(Date.UTC(2026, 10, 18, 0, index)).toISOString()}));
}

browserTest('task pagination keeps the current page on polling and clamps after cleanup', async context => {
  const {page, state, errors} = await workbench(context, [], {jobs: taskJobs(13)});
  await page.locator('#generation-tasks').click();
  const pager = page.getByRole('navigation', {name: '任务分页', exact: true});
  const rows = page.locator('.task-list .task-row');
  await pager.getByText('第 1 / 3 页', {exact: true}).waitFor();
  assert.equal(await rows.count(), 5);
  assert.equal(await rows.first().getAttribute('data-job-id'), 'task-13');
  assert.equal(await pager.getByRole('button', {name: '上一页', exact: true}).isDisabled(), true);
  await pager.getByRole('button', {name: '下一页', exact: true}).click();
  assert.equal(await rows.first().getAttribute('data-job-id'), 'task-8');
  state.jobs[7].message = '自动刷新后的任务';
  await rows.first().getByText(/自动刷新后的任务/).waitFor({timeout: 8000});
  assert.match(await pager.innerText(), /第 2 \/ 3 页/);
  await pager.getByRole('button', {name: '下一页', exact: true}).click();
  assert.equal(await rows.count(), 3);
  assert.equal(await pager.getByRole('button', {name: '下一页', exact: true}).isDisabled(), true);
  state.jobs = state.jobs.slice(0, 2);
  await page.getByRole('button', {name: '刷新任务', exact: true}).click();
  await pager.getByText('第 1 / 1 页', {exact: true}).waitFor();
  assert.equal(await rows.count(), 2);
  state.jobs = [];
  await page.getByRole('button', {name: '刷新任务', exact: true}).click();
  await pager.getByText('共 0 条 · 0–0 条', {exact: true}).waitFor();
  assert.equal(await rows.count(), 0);
  assert.equal(await pager.getByRole('button', {name: '上一页', exact: true}).isDisabled(), true);
  assert.equal(await pager.getByRole('button', {name: '下一页', exact: true}).isDisabled(), true);
  assert.deepEqual(errors, []);
});

browserTest('task filters and page size reset pagination before slicing results', async context => {
  const jobs = taskJobs(24).map((job, index) => ({...job, project_id: index % 2 + 1,
    kind: index % 3 === 0 ? 'draft' : 'outline', status: index % 4 === 0 ? 'failed' : 'completed'}));
  const {page, errors} = await workbench(context, [], {jobs,
    projects: [{id: 1, title: '寻脉'}, {id: 2, title: '远行'}]});
  await page.locator('#generation-tasks').click();
  const pager = page.getByRole('navigation', {name: '任务分页', exact: true});
  const next = pager.getByRole('button', {name: '下一页', exact: true});
  await pager.getByText('第 1 / 5 页', {exact: true}).waitFor();
  await next.click();
  await page.locator('.task-category-filters [data-task-category="outline"]').click();
  await pager.getByText('第 1 / 4 页', {exact: true}).waitFor();
  await next.click();
  await page.locator('.task-status-filters [data-task-status="completed"]').click();
  await pager.getByText('第 1 / 3 页', {exact: true}).waitFor();
  await next.click();
  await page.locator('.task-project-filters [data-task-project="2"]').click();
  await pager.getByText('第 1 / 2 页', {exact: true}).waitFor();
  await next.click();
  await page.getByLabel('每页任务数', {exact: true}).selectOption('10');
  await pager.getByText('第 1 / 1 页', {exact: true}).waitFor();
  const ids = await page.locator('.task-list .task-row').evaluateAll(rows => rows.map(row => row.dataset.jobId));
  assert.deepEqual(ids, [...jobs].reverse().filter(job => job.project_id === 2 && job.kind === 'outline' && job.status === 'completed').map(job => job.id));
  assert.deepEqual(errors, []);
});

browserTest('task panel contains the scrollable list and footer within the desktop viewport', async context => {
  const {page, errors} = await workbench(context, [], {jobs: taskJobs(24).map(job => ({...job,
    title: job.title.repeat(12), message: '较长的任务说明'.repeat(30)}))});
  await page.locator('#generation-tasks').click();
  await page.getByLabel('每页任务数', {exact: true}).selectOption('20');
  for (const viewport of [{width: 1440, height: 900}, {width: 1280, height: 720}, {width: 1024, height: 600}]) {
    await page.setViewportSize(viewport);
    const bounds = await page.evaluate(() => {
      const rect = selector => {
        const {top, bottom, left, right, height} = document.querySelector(selector).getBoundingClientRect();
        return {top, bottom, left, right, height};
      };
      const list = document.querySelector('.task-list');
      return {panel: rect('.task-progress-panel'), list: rect('.task-list'), footer: rect('.task-pagination'),
        directory: rect('.task-project-filter-group'), rail: rect('.rail'),
        scrollHeight: document.documentElement.scrollHeight, scrollWidth: document.documentElement.scrollWidth,
        listScrolls: list.scrollHeight > list.clientHeight};
    });
    assert.ok(bounds.panel.bottom <= viewport.height + 1, JSON.stringify(bounds));
    assert.ok(bounds.list.bottom <= bounds.footer.top + 1, JSON.stringify(bounds));
    assert.ok(bounds.footer.bottom <= bounds.panel.bottom, JSON.stringify(bounds));
    assert.ok(bounds.directory.top === 0 && bounds.directory.height === bounds.rail.height, JSON.stringify(bounds));
    assert.ok(bounds.scrollHeight <= viewport.height + 1, JSON.stringify(bounds));
    assert.ok(bounds.scrollWidth <= viewport.width, JSON.stringify(bounds));
    assert.ok(bounds.listScrolls);
  }
  await page.setViewportSize({width: 390, height: 844});
  assert.equal(await page.getByRole('navigation', {name: '任务分页', exact: true}).isVisible(), true);
  assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  assert.deepEqual(errors, []);
});

browserTest('admin can filter owners and read other novels without editing actions', async context => {
  const {page, state, posts, errors} = await workbench(context, [{id: 7, name: '舟客', role: '配角', summary: '渡河'}], {
    account: {id: 1, is_admin: true}, projects: [
      {id: 2, title: '自己的小说', account_id: 1, owner_username: 'writer', owner_display_name: '作者'},
      {id: 1, title: '寻脉', account_id: 2, owner_username: 'other', owner_display_name: '另一位作者'},
    ],
  });
  await page.setViewportSize({width: 390, height: 844});
  assert.match(await page.locator('#account-name').innerText(), /管理员/);
  assert.equal(await page.locator('#library-title').innerText(), '全部账号的小说');
  assert.deepEqual(await page.locator('.project-group-title').allTextContents(), ['我的小说', '其他账号小说']);
  assert.match(await page.locator('.project-group').nth(0).innerText(), /自己的小说/);
  assert.match(await page.locator('.project-group').nth(1).innerText(), /寻脉/);
  assert.match(await page.locator('#projects').innerText(), /另一位作者（other）/);
  await page.getByText('管理员只读查看：可阅读和导出，编辑与生成由所属账号操作。', {exact: true}).waitFor();
  await page.getByRole('button', {name: '角色', exact: true}).click();
  await page.getByRole('heading', {name: '角色档案 · 1 位'}).waitFor();
  assert.equal(await page.getByRole('button', {name: '生成阶段档案', exact: true}).count(), 0);
  assert.equal(await page.getByRole('button', {name: '生成主角与重要配角', exact: true}).count(), 0);
  await page.getByRole('button', {name: '大纲与分卷', exact: true}).click();
  await page.getByRole('heading', {name: '分卷目录', exact: true}).waitFor();
  for (const name of ['生成新大纲', '重写大纲', '生成本卷配角', '规划章节 →']) {
    assert.equal(await page.getByRole('button', {name, exact: true}).count(), 0);
  }
  state.chapter = {...state.chapter, content: '其他账号保存的正文。', word_count: 11, status: 'draft'};
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  await page.getByRole('article', {name: '章节正文'}).waitFor();
  assert.match(await page.getByRole('article', {name: '章节正文'}).innerText(), /其他账号保存的正文/);
  for (const name of ['编辑章节', '重写正文', '审读本章', '一致性核对']) {
    assert.equal(await page.getByRole('button', {name, exact: true}).count(), 0);
  }
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  await page.getByRole('heading', {name: '导出小说', exact: true}).waitFor();
  await page.locator('#cancel-action').click();
  await page.locator('#library-owner').selectOption('1');
  assert.equal(await page.locator('#projects .project-link').count(), 1);
  assert.match(await page.locator('#projects').innerText(), /自己的小说/);
  await page.locator('#projects .project-link').click();
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  await page.getByRole('button', {name: '编辑章节', exact: true}).waitFor();
  assert.equal(await page.getByRole('button', {name: '一致性核对', exact: true}).count(), 1);
  assert.equal(posts.length, 0);
  assert.deepEqual(errors, []);
});

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

browserTest('outline rewrite submits selected version, volume count and instructions', async context => {
  const {page, posts, errors} = await workbench(context);
  await page.getByRole('button', {name: '大纲与分卷', exact: true}).click();
  await page.getByRole('button', {name: '重写大纲', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  assert.match(await dialog.innerText(), /基于大纲 v1/);
  assert.match(await dialog.innerText(), /保存为新版本/);
  assert.equal(await dialog.getByLabel('重写后的分卷数').inputValue(), '2');
  await dialog.getByLabel('重写后的分卷数').fill('3');
  await dialog.getByLabel('本版大纲主题', {exact: true}).selectOption('comeback');
  await dialog.getByLabel('重写要求（可选）', {exact: true}).fill('保留主线，加强第二卷冲突');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0], {endpoint: '/writing/outlines/1/rewrite/tasks',
    data: {volume_count: 3, outline_theme: 'comeback', instruction: '保留主线，加强第二卷冲突'}});
  assert.deepEqual(errors, []);
});

browserTest('new outline submits the selected theme', async context => {
  const {page, posts, errors} = await workbench(context);
  await page.getByRole('button', {name: '大纲与分卷', exact: true}).click();
  await page.getByRole('button', {name: '生成新大纲', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByLabel('分卷数', {exact: true}).fill('4');
  await dialog.getByLabel('本版大纲主题', {exact: true}).selectOption('survival');
  await dialog.getByLabel('补充要求（可选）', {exact: true}).fill('每卷都要有倒计时和资源取舍');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0], {endpoint: '/writing/projects/1/outline/tasks',
    data: {volume_count: 4, outline_theme: 'survival', instruction: '每卷都要有倒计时和资源取舍'}});
  assert.deepEqual(errors, []);
});

browserTest('existing body offers rewrite and submits requirements with target length', async context => {
  const {page, state, posts, errors} = await workbench(context, [{id: 7, name: '舟客', role: '配角', summary: '渡河'}]);
  state.chapter = {...state.chapter, status: 'reviewed', content: '舟客撑船渡河。'.repeat(30), word_count: 500};
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  assert.equal(await page.getByRole('button', {name: '生成正文', exact: true}).count(), 0);
  await page.getByRole('button', {name: '重写正文', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByRole('heading', {name: '重写正文', exact: true}).waitFor();
  assert.match(await dialog.innerText(), /替换当前正文并设为草稿/);
  assert.equal(await dialog.getByLabel('目标篇幅（字符）', {exact: true}).inputValue(), '500');
  await dialog.getByLabel('目标篇幅（字符）', {exact: true}).fill('1500');
  await dialog.getByLabel('重写要求（可选）', {exact: true}).fill('保留情节，加强对话');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0], {endpoint: '/writing/chapters/1/rewrite/tasks',
    data: {target_characters: 1500, instruction: '保留情节，加强对话'}});
  assert.deepEqual(errors, []);
});

browserTest('body rewrite checks characters before submitting and allows cancel', async context => {
  const {page, state, posts, errors} = await workbench(context);
  state.chapter = {...state.chapter, status: 'draft', content: '舟客撑船渡河。'.repeat(30), word_count: 500};
  await page.getByRole('button', {name: '章节与正文', exact: true}).click();
  await page.getByRole('button', {name: '重写正文', exact: true}).click();
  await page.getByRole('heading', {name: '生成主角与重要配角', exact: true}).waitFor();
  assert.equal(posts.length, 0);
  await page.locator('#cancel-action').click();
  assert.equal(state.chapter.content, '舟客撑船渡河。'.repeat(30));
  assert.deepEqual(errors, []);
});

browserTest('novel export downloads ZIP with the chosen chapter format and outline', async context => {
  const {page, state, posts, errors} = await workbench(context);
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  assert.equal(await dialog.getByLabel('章节文件格式').inputValue(), 'txt');
  assert.equal(await dialog.getByLabel('附带大纲').inputValue(), '');
  const textDownload = page.waitForEvent('download');
  await page.locator('#action-submit').click();
  const txt = await textDownload;
  assert.equal(txt.suggestedFilename(), '寻脉.zip');
  const txtContent = await readFile(await txt.path());
  assert.equal(txtContent.subarray(0, 4).toString('hex'), '504b0304');
  await dialog.waitFor({state: 'hidden'});
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  await dialog.getByLabel('章节文件格式').selectOption('md');
  await dialog.getByLabel('附带大纲').selectOption('1');
  const markdownDownload = page.waitForEvent('download');
  await page.locator('#action-submit').click();
  const md = await markdownDownload;
  assert.equal(md.suggestedFilename(), '寻脉.zip');
  assert.equal((await readFile(await md.path())).subarray(0, 4).toString('hex'), '504b0304');
  assert.deepEqual(state.exports, [
    {params: {format: 'txt'}, authorization: 'Bearer test-session'},
    {params: {format: 'md', outline_id: '1'}, authorization: 'Bearer test-session'},
  ]);
  assert.equal(posts.length, 0);
  assert.deepEqual(errors, []);
});

browserTest('batch export keeps chapter selection across pages and sends only selected IDs', async context => {
  const {page, state, errors} = await workbench(context);
  state.chapters = Array.from({length: 17}, (_, i) => ({...state.chapter, id: i + 1,
    chapter_number: i + 1, title: `章节${i + 1}`, content: i === 16 ? '' : `正文${i + 1}`}));
  await page.setViewportSize({width: 390, height: 844});
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByLabel('导出范围').selectOption('selected');
  await dialog.getByLabel('导出第 1 章', {exact: true}).check();
  await dialog.getByRole('button', {name: '下一页', exact: true}).click();
  await dialog.getByLabel('导出第 16 章', {exact: true}).check();
  assert.equal(await dialog.getByLabel('导出第 17 章', {exact: true}).isDisabled(), true);
  await dialog.getByRole('button', {name: '上一页', exact: true}).click();
  assert.equal(await dialog.getByLabel('导出第 1 章', {exact: true}).isChecked(), true);
  await dialog.getByText('已选择 2 章（可跨页选择）', {exact: true}).waitFor();
  await dialog.getByLabel('章节文件格式').selectOption('md');
  await dialog.getByLabel('附带大纲').selectOption('1');
  const downloaded = page.waitForEvent('download');
  await page.locator('#action-submit').click();
  assert.equal((await downloaded).suggestedFilename(), '寻脉.zip');
  assert.deepEqual(state.exports, [{params: {format: 'md', outline_id: 1, chapter_ids: [1, 16]},
    authorization: 'Bearer test-session'}]);
  assert.deepEqual(errors, []);
});

browserTest('batch export requires a selection and supports select-page and clear', async context => {
  const {page, state, errors} = await workbench(context);
  state.chapter.content = '已保存的正文';
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByLabel('导出范围').selectOption('selected');
  await dialog.getByLabel('导出第 1 章', {exact: true}).waitFor();
  await page.locator('#action-submit').click();
  await page.locator('#action-error').waitFor({state: 'visible'});
  assert.match(await page.locator('#action-error').innerText(), /请至少选择一个有正文的章节/);
  assert.equal(state.exports.length, 0);
  await dialog.getByRole('button', {name: '全选本页', exact: true}).click();
  assert.equal(await dialog.getByLabel('导出第 1 章', {exact: true}).isChecked(), true);
  await dialog.getByRole('button', {name: '清空选择', exact: true}).click();
  assert.equal(await dialog.getByLabel('导出第 1 章', {exact: true}).isChecked(), false);
  await dialog.getByLabel('导出范围').selectOption('all');
  const downloaded = page.waitForEvent('download');
  await page.locator('#action-submit').click();
  await downloaded;
  assert.deepEqual(state.exports[0].params, {format: 'txt'});
  assert.deepEqual(errors, []);
});

browserTest('Android batch export passes selected IDs to the native download', async context => {
  const {page, state, errors} = await workbench(context);
  state.chapter.content = '已保存的正文';
  await page.evaluate(() => {
    window.nativeExports = [];
    window.Capacitor = {isNativePlatform: () => true, isPluginAvailable: () => true,
      registerPlugin: () => ({saveNovel: async options => {
        window.nativeExports.push(options);
        return {saved: true, filename: '寻脉.zip'};
      }})};
  });
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByLabel('导出范围').selectOption('selected');
  await dialog.getByLabel('导出第 1 章', {exact: true}).check();
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(await page.evaluate(() => window.nativeExports), [{
    url: '/api/v1/projects/1/export', token: 'test-session', format: 'txt', data: {format: 'txt', chapter_ids: [1]},
  }]);
  assert.deepEqual(errors, []);
});

browserTest('export without content shows server guidance and keeps export settings open', async context => {
  const {page, state, posts, errors} = await workbench(context);
  state.exportError = '暂无可导出的正文，请先保存章节正文，或选择附带大纲后导出';
  let downloads = 0;
  page.on('download', () => downloads++);
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  await page.locator('#action-submit').click();
  await page.locator('#action-error').waitFor({state: 'visible'});
  assert.match(await page.locator('#action-error').innerText(), /暂无可导出的正文/);
  assert.equal(await page.locator('#action-dialog').isVisible(), true);
  assert.equal(downloads, 0);
  assert.equal(posts.length, 0);
  assert.deepEqual(errors, []);
});

browserTest('Android export uses the native save dialog and treats cancellation as cancellation', async context => {
  const {page, state, posts, errors} = await workbench(context);
  await page.evaluate(() => {
    window.nativeExports = [];
    window.nativeResult = {saved: true, filename: '寻脉.zip'};
    const plugin = {saveNovel: async options => {
      window.nativeExports.push(options);
      return window.nativeResult;
    }};
    window.Capacitor = {isNativePlatform: () => true, isPluginAvailable: name => name === 'NovelExport',
      registerPlugin: () => plugin};
  });
  let downloads = 0;
  page.on('download', () => downloads++);
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  assert.equal(await page.locator('#action-submit').innerText(), '导出并选择保存位置');
  await dialog.getByLabel('章节文件格式').selectOption('md');
  await dialog.getByLabel('附带大纲').selectOption('1');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.match(await page.locator('#notice').innerText(), /小说已保存：寻脉.zip/);
  assert.deepEqual(await page.evaluate(() => window.nativeExports), [{
    url: '/api/v1/projects/1/export?format=md&outline_id=1', token: 'test-session', format: 'md',
  }]);
  await page.evaluate(() => { window.nativeResult = {cancelled: true}; });
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.equal(await page.locator('#notice').innerText(), '已取消导出保存。');
  assert.equal(downloads, 0);
  assert.equal(posts.length, 0);
  assert.equal(state.exports.length, 0);
  assert.deepEqual(errors, []);
});

browserTest('Android export expires the current login when the native request returns 401', async context => {
  const {page, errors} = await workbench(context);
  await page.evaluate(() => {
    window.Capacitor = {isNativePlatform: () => true, isPluginAvailable: () => true,
      registerPlugin: () => ({saveNovel: async () => { throw {code: '401', message: '请重新登录'}; }})};
  });
  await page.getByRole('button', {name: '导出小说', exact: true}).click();
  await page.locator('#action-submit').click();
  await page.locator('#account-dialog').waitFor({state: 'visible'});
  assert.equal(await page.evaluate(() => sessionStorage.getItem('mojian-session')), null);
  assert.deepEqual(errors, []);
});

browserTest('consistency report shows the archives actually loaded and labels related citations', async context => {
  const {page, state, errors} = await workbench(context);
  await page.setViewportSize({width: 390, height: 844});
  state.jobs = [{id: 'consistency', kind: 'consistency', project_id: 1, resource_id: 1,
    title: '一致性核对 · 1 章', status: 'completed', message: '已完成', created_at: '2026-11-15T12:00:00Z'}];
  const sources = [
    {kind: 'outline', record_id: 1, label: '大纲 v1'},
    {kind: 'character', record_id: 7, label: '舟客'},
    {kind: 'character_relationship', record_id: 3, label: '舟客 → 林川'},
    {kind: 'power_system', record_id: 1, label: '御水'},
  ];
  state.consistencyReport = {project_id: 1, outline_id: 1, outline_version: 1, checked_at: '2026-11-15T12:00:00Z',
    verdict: 'drifted', summary: '人物关系与档案冲突', limitations: [],
    chapters: [{chapter_id: 1, chapter_number: 1, title: '渡河'}], references: [sources[2]], reference_materials: sources,
    issues: [{category: 'character', severity: 'major', title: '人物关系发生无依据的变化',
      evidence: [{chapter_id: 1, quote: '舟客从未见过林川。'}],
      basis: {kind: 'character_relationship', record_id: 3, field: 'description', quote: '两人是同行伙伴'},
      explanation: '正文的初识描述与档案冲突。', suggestion: '依据同行经历调整对话。'}]};
  await page.locator('#generation-tasks').click();
  await page.getByRole('button', {name: /^全部状态/}).click();
  await page.getByRole('button', {name: '查看进度', exact: true}).click();
  const report = page.locator('#task-progress-result');
  await report.getByRole('heading', {name: '一致性核对报告', exact: true}).waitFor();
  const text = await report.innerText();
  assert.match(text, /已读取的对照档案/);
  assert.match(text, /人物档案 · 舟客/);
  assert.match(text, /人物关系 · 舟客 → 林川/);
  assert.match(text, /力量体系 · 御水/);
  assert.match(text, /对照依据 · 人物关系 · 舟客 → 林川 · 描述/);
  assert.doesNotMatch(text, /undefined/);
  assert.deepEqual(errors, []);
});

browserTest('consistency suggestions can be saved, reopened and adopted for the cited chapter', async context => {
  const {page, state, posts, errors} = await workbench(context);
  await page.setViewportSize({width: 390, height: 844});
  state.jobs = [{id: 'consistency', kind: 'consistency', project_id: 1, resource_id: 1,
    title: '一致性核对 · 1 章', status: 'completed', message: '已完成', created_at: '2026-11-19T12:00:00Z'}];
  state.consistencyReport = {job_id: 'consistency', project_id: 1, outline_id: 1, outline_version: 1,
    checked_at: '2026-11-19T12:00:00Z', verdict: 'drifted', summary: '人物关系与档案冲突', limitations: [],
    chapters: [{chapter_id: 1, chapter_number: 1, title: '渡河'}, {chapter_id: 4, chapter_number: 4, title: '远行'}],
    references: [{kind: 'character_relationship', record_id: 3, label: '舟客 → 林川'}],
    issues: [{category: 'character', severity: 'major', title: '人物关系发生无依据的变化',
      evidence: [{chapter_id: 1, quote: '舟客从未见过林川。'}],
      basis: {kind: 'character_relationship', record_id: 3, field: 'description', quote: '两人是同行伙伴'},
      explanation: '正文的初识描述与档案冲突。', suggestion: '依据同行经历调整对话。'}]};
  await page.locator('#generation-tasks').click();
  await page.getByRole('button', {name: '查看进度', exact: true}).click();
  const report = page.locator('#task-progress-result');
  await report.getByRole('button', {name: '编辑修复意见', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  const edited = '保留渡河场景，改为两人回忆上一次同行。';
  await dialog.getByLabel('修复意见', {exact: true}).fill(edited);
  await dialog.getByRole('button', {name: '保存修复意见', exact: true}).click();
  await dialog.waitFor({state: 'hidden'});
  await report.getByText(`修复意见：${edited}`, {exact: true}).waitFor();
  assert.deepEqual(posts, [{endpoint: '/writing/projects/1/consistency/consistency/issues/0', method: 'PATCH', data: {suggestion: edited}}]);
  await report.getByRole('button', {name: '采纳并修复', exact: true}).click();
  assert.equal(await dialog.getByLabel('修复意见', {exact: true}).inputValue(), edited);
  assert.match(await dialog.locator('#action-description').innerText(), /第 1 章 · 渡河/);
  assert.doesNotMatch(await dialog.locator('#action-description').innerText(), /第 4 章/);
  const approved = edited + '并保留原有对白语气。';
  await dialog.getByLabel('修复意见', {exact: true}).fill(approved);
  await dialog.getByRole('button', {name: '采纳并开始修复', exact: true}).click();
  await dialog.waitFor({state: 'hidden'});
  await report.getByRole('button', {name: '修复中', exact: true}).waitFor();
  assert.equal(await report.getByRole('button', {name: '编辑修复意见', exact: true}).isDisabled(), true);
  assert.deepEqual(posts[1], {endpoint: '/writing/projects/1/consistency/consistency/issues/0/repair/tasks', data: {suggestion: approved}});
  // Reopening the original job replays the saved edit and adoption state.
  await page.locator('#back-to-tasks').click();
  await page.locator('.task-row[data-job-id="consistency"]').getByRole('button', {name: '查看进度', exact: true}).click();
  await report.getByText(`修复意见：${approved}`, {exact: true}).waitFor();
  assert.equal(await report.getByRole('button', {name: '修复中', exact: true}).isDisabled(), true);
  state.jobs.find(job => job.id === 'repair').status = 'completed';
  state.consistencyReport.issues[0].repair_status = 'completed';
  state.repairResult = {project_id: 1, completed_count: 1, chapters: [{chapter_id: 1, chapter_number: 1, title: '渡河', word_count: 200}]};
  state.chapter.content = '舟客提起与林川上一次同行的经历，笑着撑开竹篙。';
  state.chapter.status = 'draft';
  // Polling updates the report's adoption status without reopening it.
  await report.getByRole('button', {name: '已修复', exact: true}).waitFor({timeout: 8000});
  await report.getByRole('button', {name: '查看修复进度', exact: true}).click();
  await report.getByText(/已按采纳的修复意见保存 1 章正文/).waitFor();
  await report.getByRole('button', {name: '打开小说查看结果', exact: true}).click();
  await page.getByRole('article', {name: '章节正文', exact: true}).waitFor();
  assert.equal(await page.getByRole('article', {name: '章节正文', exact: true}).innerText(), state.chapter.content);
  assert.deepEqual(errors, []);
});

browserTest('character dossiers display planned actions, results and current progress, and regenerate by volume', async context => {
  const {page, state, posts, errors} = await workbench(context, [{id: 7, name: '舟客', role: '配角', summary: '渡河向导'}]);
  await page.setViewportSize({width: 390, height: 844});
  state.profiles = [{id: 3, character_id: 7, volume_id: 1, volume_number: 1, volume_title: '寻脉卷',
    scenes: '【本部分行动（计划）】\n帮助林川渡河\n【阶段结果（计划）】\n取得信任\n【与小说进度的关系（生成时）】\n推进渡河节点，留下寻脉线索',
    progress: {planned_chapters: 3, written_chapters: 1, last_written_chapter: 1}}];
  await page.getByRole('button', {name: '角色', exact: true}).click();
  const card = page.locator('.character-card');
  await card.locator('summary').click();
  assert.match(await card.innerText(), /本部分行动（计划）/);
  assert.match(await card.innerText(), /阶段结果（计划）/);
  assert.match(await card.innerText(), /本卷 3 章中有 1 章已保存正文/);
  assert.match(await card.innerText(), /不代表角色阶段结果已发生/);
  await card.getByRole('button', {name: '生成阶段档案', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByRole('heading', {name: '生成角色阶段档案', exact: true}).waitFor();
  assert.equal(await dialog.getByRole('checkbox').isChecked(), true);
  await dialog.getByLabel('目标分卷').selectOption('2');
  await dialog.getByLabel('补充要求（可选）').fill('承接上一卷结尾，写清代价');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0], {endpoint: '/writing/volumes/2/character-profiles/tasks',
    data: {character_ids: [7], instruction: '承接上一卷结尾，写清代价'}});
  assert.deepEqual(errors, []);
});

browserTest('chapter facts show actions, current state and quotations separately from planned outcomes', async context => {
  const {page, state, errors} = await workbench(context, [{id: 7, name: '舟客', role: '配角', summary: '渡河向导'}]);
  await page.setViewportSize({width: 390, height: 844});
  state.profiles = [{character_id: 7, volume_number: 1, volume_title: '寻脉卷',
    scenes: '【阶段结果（计划）】\n成为林川的盟友', progress: {planned_chapters: 3, written_chapters: 1}}];
  state.characterProgress = [{character_id: 7, chapter_number: 1, title: '渡河',
    actions: '帮助林川渡河，左臂受伤', current_state: '左臂受伤，停在北岸', relationships: '与林川初步互信',
    evidence: ['舟客护着林川登岸，左臂被碎石划伤。']}];
  await page.getByRole('button', {name: '角色', exact: true}).click();
  const card = page.locator('.character-card');
  await card.locator(':scope > summary').click();
  await card.getByRole('heading', {name: '正文进展（已发生）', exact: true}).waitFor();
  const fact = card.locator('.character-fact');
  await fact.locator(':scope > summary').click();
  assert.match(await fact.innerText(), /帮助林川渡河，左臂受伤/);
  assert.match(await fact.innerText(), /截至本章状态/);
  assert.match(await fact.innerText(), /左臂受伤，停在北岸/);
  assert.match(await fact.innerText(), /与林川初步互信/);
  assert.doesNotMatch(await fact.innerText(), /成为林川的盟友/);
  await fact.getByText('正文依据', {exact: true}).click();
  assert.equal(await fact.locator('blockquote').innerText(), '舟客护着林川登岸，左臂被碎石划伤。');
  assert.match(await card.innerText(), /阶段结果（计划）/);
  state.characterProgress = [];
  await page.reload();
  await page.getByRole('button', {name: '角色', exact: true}).click();
  await page.getByRole('heading', {name: '角色档案 · 1 位', exact: true}).waitFor();
  assert.equal(await page.locator('.character-fact').count(), 0);
  await card.locator(':scope > summary').click();
  assert.match(await card.innerText(), /尚无已分析的正文进展/);
  assert.deepEqual(errors, []);
});

browserTest('chapter fact history loads another page without dropping existing records', async context => {
  const {page, state, errors} = await workbench(context, [{id: 7, name: '舟客', role: '配角', summary: '渡河向导'}]);
  state.characterProgress = Array.from({length: 101}, (_, index) => ({character_id: 7,
    chapter_number: 101 - index, title: `行程${101 - index}`, actions: '渡河', current_state: '在北岸', relationships: '', evidence: []}));
  await page.getByRole('button', {name: '角色', exact: true}).click();
  await page.getByRole('button', {name: '加载更多正文进展', exact: true}).waitFor();
  assert.equal(await page.locator('.character-fact').count(), 100);
  await page.getByRole('button', {name: '加载更多正文进展', exact: true}).click();
  await page.getByRole('button', {name: '加载更多正文进展', exact: true}).waitFor({state: 'hidden'});
  assert.equal(await page.locator('.character-fact').count(), 101);
  assert.match(await page.locator('.character-fact').last().textContent(), /第 1 章 · 行程1/);
  assert.deepEqual(errors, []);
});

browserTest('existing role profile generation requires an explicit character selection', async context => {
  const {page, posts, errors} = await workbench(context, [{id: 7, name: '林川', role: '主角', summary: '寻脉者'}]);
  await page.getByRole('button', {name: '大纲与分卷', exact: true}).click();
  await page.getByRole('button', {name: '生成已有角色阶段档案', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  await dialog.getByRole('heading', {name: '生成角色阶段档案', exact: true}).waitFor();
  await page.locator('#action-submit').click();
  await page.locator('#action-error').waitFor({state: 'visible'});
  assert.match(await page.locator('#action-error').innerText(), /请选择 1—20 位角色/);
  assert.equal(posts.length, 0);
  await dialog.getByRole('checkbox', {name: /林川/}).check();
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.deepEqual(posts[0], {endpoint: '/writing/volumes/1/character-profiles/tasks', data: {character_ids: [7], instruction: ''}});
  assert.deepEqual(errors, []);
});

browserTest('core role generation uses the selected outline and can defer stage dossiers until an outline exists', async context => {
  const {page, posts, errors} = await workbench(context);
  await page.getByRole('button', {name: '角色', exact: true}).click();
  await page.getByRole('button', {name: '生成主角与重要配角', exact: true}).click();
  const dialog = page.locator('#action-dialog');
  assert.equal(await dialog.getByLabel('依据大纲').inputValue(), '1');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.equal(posts[0].data.outline_id, 1);
  await page.getByRole('button', {name: '生成主角与重要配角', exact: true}).click();
  await dialog.getByLabel('依据大纲').selectOption('');
  await page.locator('#action-submit').click();
  await dialog.waitFor({state: 'hidden'});
  assert.equal(Object.hasOwn(posts[1].data, 'outline_id'), false);
  assert.deepEqual(errors, []);
});
