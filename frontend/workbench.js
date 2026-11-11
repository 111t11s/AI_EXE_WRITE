import {resolveApiBaseUrl} from './api-client.js';

const API_BASE_URL = resolveApiBaseUrl(window.AITXT_CONFIG?.apiBaseUrl);
let accessKey = '';
try { accessKey = sessionStorage.getItem('mojian-access-key') || ''; } catch { /* optional */ }
const $ = (id) => document.getElementById(id);
const statusNames = {planning: '规划中', writing: '写作中', completed: '已完成', paused: '暂停',
  planned: '待写作', draft: '草稿', reviewed: '已审读', published: '已发布',
  approved: '已确认', active: '使用中', archived: '已归档'};
const labels = {genre: '小说类型', setting: '时代与背景', power_system: '力量体系', cheat: '金手指',
  protagonist: '主角设定', goal: '主角目标', tone: '故事基调', length: '篇幅规划'};
const pageSize = 15;
const state = {projects: [], project: null, worlds: [], outlines: [], tab: 'overview',
  outline: null, volumes: null, volume: null, beats: null, page: null, chapter: null, chapterId: null,
  busy: false, reviews: new Map()};
const sequence = {};
const view = $('view');

function element(tag, props = {}, ...children) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (key === 'click') node.addEventListener('click', value);
    else if (key === 'change') node.addEventListener('change', value);
    else node.setAttribute(key, value === true ? '' : String(value));
  }
  node.append(...children.flat().filter((item) => item != null));
  return node;
}

const button = (text, click, style = 'secondary', disabled = false) =>
  element('button', {type: 'button', class: `button ${style}`, click, disabled}, text);
const pill = (text, warm = false) => element('span', {class: `pill${warm ? ' warm' : ''}`}, text);
const prose = (text) => element('p', {class: 'prose'}, text || '暂无内容');
const muted = (text) => element('p', {class: 'muted'}, text);
const rule = () => element('hr', {class: 'rule'});
const actions = (...items) => element('div', {class: 'actions'}, ...items);
const stack = (...items) => element('div', {class: 'stack'}, ...items);
const key = (name) => (sequence[name] = (sequence[name] ?? 0) + 1);
const current = (name, token) => sequence[name] === token;

async function api(path, {method = 'GET', data, filters} = {}) {
  const query = new URLSearchParams(filters ?? {}).toString();
  const response = await fetch(`${API_BASE_URL}${path}${query ? `?${query}` : ''}`, {
    method, cache: 'no-store',
    headers: {
      ...(accessKey ? {'X-AITXT-Access-Key': accessKey} : {}),
      ...(data !== undefined ? {'Content-Type': 'application/json'} : {}),
    },
    ...(data !== undefined ? {body: JSON.stringify(data)} : {}),
  });
  const body = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail) ? detail.map((item) => item.msg).join('；') : detail;
    const error = new Error(typeof message === 'string' ? message : `请求失败（${response.status}）`);
    error.status = response.status;
    if (response.status === 401) showAccessDialog();
    throw error;
  }
  return body;
}

const post = (path, data = {}) => api(path, {method: 'POST', data});

async function allRows(path, filters = {}) {
  const items = [];
  let page;
  do {
    page = await api(path, {filters: {...filters, limit: 100, offset: items.length}});
    items.push(...page.items);
  } while (page.has_more && page.items.length);
  return items;
}

function notify(text = '', kind = '') {
  const node = $('notice');
  node.hidden = !text;
  node.textContent = text;
  node.dataset.kind = kind;
}

function setBusy(message = '') {
  state.busy = Boolean(message);
  $('activity').hidden = !message;
  $('activity-text').textContent = message;
  $('new-project').disabled = state.busy;
  $('refresh').disabled = state.busy;
  renderLibrary();
}

function connection(message, status) {
  $('connection').textContent = message;
  $('connection').dataset.state = status;
}

function renderLibrary() {
  $('projects').replaceChildren(...(state.projects.length ? state.projects.map((project) =>
    element('button', {type: 'button', class: 'project-link', 'aria-current': String(state.project?.id === project.id),
      disabled: state.busy, click: () => openProject(project.id)},
    element('span', {class: 'project-badge', 'aria-hidden': 'true'}, Array.from(project.title || '未')[0]),
    element('span', {}, element('strong', {}, project.title || '未命名小说'),
      element('small', {}, project.genre || '小说')))) :
    [element('p', {class: 'rail-note'}, state.libraryError ? '书架读取失败，点击刷新重试。' : '还没有小说项目。')]));
}

function rememberedProject() {
  try { return Number(localStorage.getItem('mojian-project')); } catch { return null; }
}

async function loadLibrary(preferred = state.project?.id ?? rememberedProject()) {
  const token = key('library');
  connection('连接中', 'waiting');
  try {
    const projects = await allRows('/projects');
    if (!current('library', token)) return;
    state.projects = projects.reverse();
    state.libraryError = false;
    connection('数据已连接', 'ready');
    renderLibrary();
    const target = state.projects.find((project) => project.id === preferred) ?? state.projects[0];
    if (target) await openProject(target.id);
    else { state.project = null; render(); }
  } catch (error) {
    if (!current('library', token)) return;
    state.libraryError = true;
    connection('暂未连接', 'error');
    renderLibrary();
    notify(`无法读取项目：${error.message}。请确认服务和数据库已启动。`, 'error');
    render();
  }
}

async function openProject(projectId) {
  const token = key('project');
  for (const name of ['outline', 'volume', 'chapters', 'chapter']) key(name);
  Object.assign(state, {project: state.projects.find((item) => item.id === projectId) ?? null, worlds: [], outlines: [],
    tab: 'overview', outline: null, volumes: null, volume: null, beats: null, page: null,
    chapter: null, chapterId: null, chapterLoading: false, chapterListError: null, outlineError: null,
    volumeError: null, projectLoading: true, projectError: null});
  notify();
  renderLibrary();
  render();
  try {
    const [project, worlds, outlines] = await Promise.all([
      api(`/projects/${projectId}`), allRows('/worlds', {project_id: projectId, is_current: true}),
      allRows('/outlines', {project_id: projectId}),
    ]);
    if (!current('project', token)) return;
    Object.assign(state, {project, worlds, outlines: outlines.reverse()});
    try { localStorage.setItem('mojian-project', String(projectId)); } catch { /* optional */ }
  } catch (error) {
    if (current('project', token)) state.projectError = error.message;
  } finally {
    if (current('project', token)) {
      state.projectLoading = false;
      render();
      if (!state.projectError && state.tab !== 'overview') void activate(state.tab);
    }
  }
}

function panel(title, content, subtitle, aside) {
  return element('section', {class: 'panel'}, element('header', {class: 'panel-head'},
    element('div', {}, element('h2', {}, title), subtitle ? muted(subtitle) : null), aside), content);
}

function empty(title, message, action = null) {
  return element('section', {class: 'panel empty'}, element('h2', {}, title), element('p', {}, message), action);
}

function details(...entries) {
  return element('dl', {class: 'details'}, entries.filter(([, value]) => value).map(([label, value]) =>
    element('div', {}, element('dt', {}, label), element('dd', {class: 'prose'}, value))));
}

function selectedRow(label, sub, isCurrent, click) {
  return element('button', {type: 'button', class: 'row-button', 'aria-current': String(isCurrent), click},
    element('strong', {}, label), sub ? element('small', {}, sub) : null);
}

function hero(project) {
  return element('section', {class: 'hero'}, element('p', {class: 'eyebrow'}, '小说项目'),
    element('h1', {}, project.title || '未命名小说'), project.premise ? element('p', {}, project.premise) : null,
    element('div', {class: 'hero-meta'}, pill(project.genre || '小说'), pill(statusNames[project.status] ?? project.status),
      project.tone ? pill(project.tone) : null));
}

function overview() {
  const world = state.worlds[0];
  const project = state.project;
  const steps = [['世界观', world ? `当前：${world.title}` : '先建立当前世界观'],
    ['大纲与分卷', state.outlines.length ? `已有 ${state.outlines.length} 个大纲版本` : '生成全书结构与剧情节点'],
    ['章节与正文', '先规划章节，再生成初稿或手动编辑'], ['审读', '检查人物、设定与情节衔接']];
  const worldPanel = world ? panel(world.title, stack(prose(world.summary), element('div', {class: 'details'},
    [['世界规则', world.rules], ['历史沿革', world.history], ['文化与社会', world.cultures]].filter(([, value]) => value)
      .map(([name, value]) => element('details', {}, element('summary', {}, name), prose(value))))),
  world.era, pill(`世界观 v${world.version_no}`)) : empty('尚无当前世界观', '生成大纲前需要一个当前世界观。也可以新建小说，通过问答建立世界观。');
  return element('div', {class: 'layout'}, stack(worldPanel, panel('项目设定', details(['时代与背景', project.setting],
    ['主角目标', project.protagonist_goal], ['篇幅规划', project.target_length]))),
  panel('创作路线', stack(element('ol', {class: 'steps'}, steps.map(([name, hint], index) => element('li', {},
    element('span', {class: 'step-number'}, String(index + 1)), element('div', {}, element('strong', {}, name), element('p', {}, hint))))),
  button(state.outlines.length ? '查看大纲 →' : '开始规划大纲 →', () => activate('outline'), 'primary', !world && !state.outlines.length))));
}

function render() {
  view.setAttribute('aria-busy', String(Boolean(state.projectLoading)));
  if (!state.project) {
    view.replaceChildren(empty(state.libraryError ? '暂时打不开书架' : '开始一本新小说',
      state.libraryError ? '连接恢复后刷新，即可继续上次的创作。' : '先和世界观助手聊聊，也可以从左侧打开已有项目。',
      state.libraryError ? button('刷新书架', () => loadLibrary(), 'primary') : button('新建小说', openWizard, 'primary')));
    return;
  }
  const tabs = element('nav', {class: 'tabs', 'aria-label': '项目页面'},
    [['overview', '概览'], ['outline', '大纲与分卷'], ['chapters', '章节与正文']].map(([tab, title]) =>
      element('button', {type: 'button', class: 'tab', 'aria-current': state.tab === tab ? 'page' : null,
        click: () => activate(tab)}, title)));
  let content;
  if (state.projectLoading) content = empty('正在读取项目', '正在加载世界观和大纲。');
  else if (state.projectError) content = empty('项目读取失败', state.projectError,
    button('重试', () => openProject(state.project.id)));
  else if (state.tab === 'outline') content = outlineView();
  else if (state.tab === 'chapters') content = chapterView();
  else content = overview();
  view.replaceChildren(hero(state.project), tabs, content);
}

async function activate(tab) {
  state.tab = tab;
  render();
  if (state.projectLoading || state.projectError) return;
  if (tab === 'outline' && !state.outline && state.outlines[0]) await loadOutline(state.outlines[0].id);
  if (tab === 'chapters' && !state.page && !state.chapterLoading) await loadChapters();
}

async function loadOutline(id, preferredVolume) {
  const token = key('outline');
  key('volume');
  Object.assign(state, {outline: state.outlines.find((row) => row.id === id), volumes: null, volume: null, beats: null, outlineError: null});
  render();
  try {
    const volumes = await allRows('/outline-volumes', {outline_id: id});
    if (!current('outline', token)) return;
    state.volumes = volumes;
    const next = volumes.find((item) => item.id === preferredVolume) ?? volumes[0];
    if (next) await loadVolume(next.id);
    else render();
  } catch (error) {
    if (current('outline', token)) { state.outlineError = error.message; render(); }
  }
}

async function loadVolume(id) {
  const token = key('volume');
  Object.assign(state, {volume: state.volumes.find((item) => item.id === id), beats: null, volumeError: null});
  render();
  try {
    const beats = await allRows('/plot-beats', {volume_id: id});
    if (current('volume', token)) state.beats = beats;
  } catch (error) {
    if (current('volume', token)) state.volumeError = error.message;
  } finally { if (current('volume', token)) render(); }
}

function outlineView() {
  const create = button('生成新大纲', outlineDialog, 'primary', state.busy || state.worlds.length !== 1);
  if (!state.outlines.length) return empty('还没有大纲', state.worlds.length === 1 ?
    '以当前世界观生成第一版大纲，分卷和剧情节点会一并保存。' : '需要先为项目设置唯一的当前世界观。', create);
  const versionSelect = element('select', {'aria-label': '大纲版本', change: (event) => loadOutline(Number(event.target.value))},
    state.outlines.map((row) => element('option', {value: row.id, selected: state.outline?.id === row.id},
      `版本 ${row.version_no} · ${statusNames[row.status] ?? row.status}`)));
  const header = panel('大纲版本', state.outline ? details(['全书摘要', state.outline.summary],
    ['核心冲突', state.outline.central_conflict], ['主题', state.outline.themes]) : muted('正在读取大纲…'), null,
  actions(versionSelect, create));
  if (state.outlineError) return stack(header, empty('分卷读取失败', state.outlineError, button('重试', () => loadOutline(state.outline.id))));
  if (!state.volumes) return stack(header, empty('正在读取分卷', '请稍候。'));
  if (!state.volumes.length) return stack(header, empty('这一版没有分卷', '可生成新大纲，或通过接口补充分卷。'));
  const volumeList = panel('分卷目录', element('div', {class: 'mini-list'}, state.volumes.map((item) =>
    selectedRow(`第 ${item.volume_number} 卷 · ${item.title}`, null, state.volume?.id === item.id, () => loadVolume(item.id)))));
  const v = state.volume;
  const beats = state.volumeError ? stack(muted(state.volumeError), button('重试节点', () => loadVolume(v.id))) : state.beats === null ?
    muted('正在读取剧情节点…') : state.beats.length ? element('ol', {class: 'numbered'}, state.beats.map((beat) =>
      element('li', {}, element('strong', {}, beat.title), prose(beat.summary), beat.foreshadowing ? prose(`伏笔：${beat.foreshadowing}`) : null))) : muted('这一卷暂无剧情节点。');
  const detail = v ? panel(v.title, stack(details(['分卷摘要', v.summary], ['主角目标', v.protagonist_goal], ['主要冲突', v.main_conflict],
    ['高潮', v.climax], ['结尾悬念', v.ending_hook]), rule(), element('h3', {}, '剧情节点'), beats), `第 ${v.volume_number} 卷`,
  button('规划章节 →', () => planDialog(v), 'primary', state.busy || state.worlds.length !== 1)) : empty('请选择分卷', '打开一卷查看剧情节点。');
  return stack(header, element('div', {class: 'layout reading'}, volumeList, detail));
}

async function loadChapters(offset = 0, selectedId = state.chapterId) {
  const token = key('chapters');
  Object.assign(state, {chapterLoading: true, chapterListError: null});
  render();
  try {
    const page = await api('/chapters', {filters: {project_id: state.project.id, limit: pageSize, offset}});
    if (!current('chapters', token)) return;
    state.page = page;
    const next = selectedId ?? page.items[0]?.id;
    if (next && state.chapter?.id !== next) await loadChapter(next);
  } catch (error) {
    if (current('chapters', token)) state.chapterListError = error.message;
  } finally { if (current('chapters', token)) { state.chapterLoading = false; render(); } }
}

async function loadChapter(id) {
  const token = key('chapter');
  Object.assign(state, {chapterId: id, chapter: null, chapterError: null});
  render();
  try {
    const row = await api(`/chapters/${id}`);
    if (current('chapter', token)) state.chapter = row;
  } catch (error) {
    if (current('chapter', token)) state.chapterError = error.message;
  } finally { if (current('chapter', token)) render(); }
}

function reviewView(report) {
  return panel('审读建议', stack(prose(report.summary), report.issues.map((issue) =>
    element('div', {class: 'report-issue'}, pill(issue.severity === 'major' ? '主要问题' : '细节建议', true),
      prose(`文中依据：${issue.evidence}`), prose(`修改建议：${issue.suggestion}`)))),
  '审读结果仅在当前页面显示，不会改动正文。', pill(report.verdict === 'ready' ? '可以继续' : '建议修改', report.verdict !== 'ready'));
}

function chapterView() {
  if (state.chapterListError) return empty('章节读取失败', state.chapterListError, button('重试', () => loadChapters()));
  if (!state.page) return empty('正在读取章节', '请稍候。');
  const page = state.page;
  if (!page.items.length && !page.offset) return empty('还没有章节', '到「大纲与分卷」选中一卷，再让章节规划师生成章节。',
    button('前往大纲与分卷', () => activate('outline'), 'primary'));
  const list = panel('章节目录', stack(element('div', {class: 'mini-list'}, page.items.map((item) =>
    selectedRow(`第 ${item.chapter_number} 章 · ${item.title || '未命名'}`,
      `${statusNames[item.status] ?? item.status} · ${item.word_count ?? 0} 字`, state.chapterId === item.id, () => loadChapter(item.id)))),
  page.offset || page.has_more ? element('div', {class: 'paging'},
    button('上一页', () => loadChapters(page.offset - pageSize, null), 'secondary', !page.offset || state.chapterLoading),
    `第 ${Math.floor(page.offset / pageSize) + 1} 页`,
    button('下一页', () => loadChapters(page.offset + pageSize, null), 'secondary', !page.has_more || state.chapterLoading)) : null));
  const chapter = state.chapter;
  if (!chapter) return element('div', {class: 'layout reading'}, list, empty(state.chapterError ? '无法读取章节' : '正在打开章节',
    state.chapterError || '请稍候。', state.chapterError ? button('重试', () => loadChapter(state.chapterId)) : null));
  const hasContent = Boolean(chapter.content.trim());
  const canEdit = ['planned', 'draft'].includes(chapter.status);
  const section = panel(chapter.title || '未命名章节', stack(details(['章节规划', chapter.summary]), rule(),
    hasContent ? element('article', {class: 'reader', 'aria-label': '章节正文'}, chapter.content) : muted('正文还没开始。可以生成初稿，也可以手动写。')),
  `第 ${chapter.chapter_number} 章 · ${statusNames[chapter.status] ?? chapter.status} · ${chapter.word_count ?? 0} 字`, actions(
    !hasContent && chapter.status === 'planned' ? button('生成正文', () => draftDialog(chapter), 'primary', state.busy || state.worlds.length !== 1) : null,
    canEdit ? button('编辑章节', () => editDialog(chapter), 'secondary', state.busy) : null,
    hasContent ? button('审读本章', () => reviewDialog(chapter), 'secondary', state.busy || state.worlds.length !== 1) : null));
  const report = state.reviews.get(chapter.id);
  return element('div', {class: 'layout reading'}, list, stack(section, report ? reviewView(report) : null));
}

const action = $('action-dialog');
let task = null;
const noteField = {name: 'instruction', label: '补充要求（可选）', type: 'textarea', maxlength: 2000,
  placeholder: '例如：加强主角与世界规则的冲突'};
const numberField = (name, label, value, min, max) => ({name, label, value, min, max, type: 'number', required: true});

function openAction(options) {
  if (state.busy) return;
  task = options;
  $('action-title').textContent = options.title;
  $('action-description').textContent = options.description;
  $('action-error').hidden = true;
  $('action-submit').textContent = options.submit;
  $('action-fields').replaceChildren(...options.fields.map(({label, type, value, ...attributes}, index) => {
    const input = type === 'textarea' ? element('textarea', attributes) : element('input', {...attributes, type});
    input.value = value ?? '';
    if (index === 0) input.autofocus = true;
    return element('label', {class: 'control'}, label, input);
  }));
  action.showModal();
}

function setActionBusy(busy) {
  for (const control of $('action-form').elements) control.disabled = busy;
  $('action-submit').textContent = busy ? (task.pending || '正在生成…') : task.submit;
}

function closeAction() { if (!state.busy) action.close(); }
$('close-action').onclick = closeAction;
$('cancel-action').onclick = closeAction;
action.addEventListener('cancel', (event) => { if (state.busy) event.preventDefault(); });

$('action-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.busy || !task) return;
  const submitted = task;
  const data = Object.fromEntries(new FormData(event.currentTarget));
  for (const field of submitted.fields) {
    if (field.type === 'number') data[field.name] = Number(data[field.name]);
    else if (field.name !== 'content') data[field.name] = data[field.name].trim();
  }
  $('action-error').hidden = true;
  setBusy(submitted.pending || '智能体正在处理，可能需要几分钟。');
  setActionBusy(true);
  try {
    let receipt;
    try { receipt = await submitted.send(data); }
    catch (error) {
      $('action-error').textContent = error instanceof TypeError ? '连接中断。若操作会写入数据，请刷新项目核对结果后再试。' : error.message;
      $('action-error').hidden = false;
      return;
    }
    action.close();
    notify(submitted.success(receipt));
    try { await submitted.after(receipt); }
    catch (error) { notify(`操作已完成，但刷新结果失败：${error.message}。请刷新书架。`, 'error'); }
  } finally {
    setActionBusy(false);
    setBusy();
    render();
  }
});

function outlineDialog() {
  const id = state.project.id;
  openAction({title: '生成新大纲', description: '根据当前世界观创建一个新版本，已有大纲会保留。生成可能需要几分钟。',
    submit: '生成并保存', fields: [numberField('volume_count', '分卷数', 3, 1, 5), noteField],
    send: (data) => post(`/writing/projects/${id}/outline`, data),
    success: (result) => `大纲 v${result.version_no} 已保存，包含 ${result.volume_ids.length} 卷、${result.plot_beat_count} 个剧情节点。`,
    after: async (result) => {
      state.outlines = (await allRows('/outlines', {project_id: id})).reverse();
      await loadOutline(result.outline_id);
    },
  });
}

function planDialog(volume) {
  const page = state.page;
  const start = page && !page.has_more ? Math.min((page.items.at(-1)?.chapter_number ?? 0) + 1, 4294967295) : 1;
  openAction({title: '规划章节', description: `把《${volume.title}》的剧情节点拆成章节。章号在全书内唯一，已有章节不会被覆盖。`,
    submit: '规划并保存', fields: [numberField('start_chapter', '起始章号', start, 1, 4294967295),
      numberField('chapter_count', '生成章节数', 5, 1, 12), noteField],
    send: (data) => post(`/writing/volumes/${volume.id}/chapter-plan`, data),
    success: (result) => `已保存 ${result.chapters.length} 个计划章节，选中任一章节即可生成正文。`,
    after: async (result) => { state.tab = 'chapters'; await loadChapters(0, result.chapters[0].chapter_id); },
  });
}

function draftDialog(chapter) {
  openAction({title: '生成正文', description: `为第 ${chapter.chapter_number} 章生成初稿，正文会直接保存到当前章节。`,
    submit: '写作并保存', fields: [numberField('target_characters', '目标篇幅（字符）', 2000, 300, 6000), noteField],
    send: (data) => post(`/writing/chapters/${chapter.id}/draft`, data),
    success: (result) => `第 ${chapter.chapter_number} 章初稿已保存，共 ${result.word_count} 字。`,
    after: () => refreshChapter(chapter.id),
  });
}

function reviewDialog(chapter) {
  openAction({title: '审读本章', description: '检查世界规则、人物动机、前文衔接和章节目标。审读只给建议，不会修改正文。',
    submit: '开始审读', fields: [noteField], send: (data) => post(`/writing/chapters/${chapter.id}/review`, data),
    success: () => '审读完成，建议显示在正文下方。',
    after: (report) => { state.reviews.set(chapter.id, report); },
  });
}

function editDialog(chapter) {
  openAction({title: '编辑章节', description: '保存标题、章节规划和正文。已有审读建议会清除。',
    submit: '保存修改', pending: '正在保存…', fields: [
      {name: 'title', label: '标题', value: chapter.title, maxlength: 200, type: 'text'},
      {name: 'summary', label: '章节规划', value: chapter.summary, type: 'textarea', maxlength: 8000},
      {name: 'content', label: '正文', value: chapter.content, type: 'textarea', rows: 10, maxlength: Math.max(30000, chapter.content.length)},
    ],
    send: ({title, summary, content}) => {
      if (chapter.status === 'draft' && !content.trim()) throw new Error('草稿正文不能为空。');
      return api(`/chapters/${chapter.id}`, {method: 'PATCH', data: {title, summary, content,
        word_count: Array.from(content).filter((letter) => !/\s/u.test(letter)).length,
        ...(chapter.status === 'planned' && content.trim() ? {status: 'draft'} : {}),
      }});
    },
    success: () => '章节修改已保存。',
    after: async () => { state.reviews.delete(chapter.id); await refreshChapter(chapter.id); },
  });
}

async function refreshChapter(id) {
  await loadChapter(id);
  await loadChapters(state.page?.offset ?? 0, id);
}

const wizard = $('wizard');
let session = {id: null, values: {}, done: false, busy: false, projectId: null};

function syncWizard() {
  const {id, values, done, busy, projectId} = session;
  const count = Object.keys(labels).filter((name) => values[name]).length;
  $('slot-count').textContent = `${count} / ${Object.keys(labels).length}`;
  $('slots').replaceChildren(...Object.entries(labels).map(([name, label]) =>
    element('div', {}, element('dt', {}, label), element('dd', {}, values[name] || '待补充'))));
  $('chat-input').disabled = !id || busy || done;
  $('chat-send').disabled = !id || busy || done;
  $('confirm-world').disabled = !id || busy || done || count !== Object.keys(labels).length;
  $('confirm-world').textContent = busy && count === Object.keys(labels).length ? '处理中…' : '确认并生成';
  $('restart-wizard').disabled = busy;
  $('close-wizard').disabled = busy;
  $('open-created').hidden = !projectId;
}

function bubble(text, user = false) {
  $('chat').append(element('p', {class: `bubble${user ? ' user' : ''}`}, text));
  $('chat').scrollTop = $('chat').scrollHeight;
}

async function startConversation() {
  session = {id: null, values: {}, done: false, busy: true, projectId: null};
  $('chat').replaceChildren();
  $('wizard-preview').hidden = true;
  $('chat-input').value = '';
  syncWizard();
  try {
    const reply = await post('/novel-assistant/sessions');
    session.id = reply.session_id;
    session.values = reply.values;
    bubble(reply.message);
  } catch (error) { bubble(`无法开始对话：${error.message}。可点击「开始新对话」重试。`); }
  finally { session.busy = false; syncWizard(); }
}

function openWizard() {
  if (state.busy) return;
  wizard.showModal();
  if (!session.id) void startConversation();
  else syncWizard();
}

async function sendMessage(content) {
  if (!content || !session.id || session.busy || session.done) return;
  session.busy = true;
  syncWizard();
  bubble(content, true);
  const generating = ['确认生成', '确认', '开始生成'].includes(content);
  if (generating) bubble('正在生成世界观，可能需要几分钟。');
  try {
    const reply = await post(`/novel-assistant/sessions/${session.id}/messages`, {content});
    session.values = reply.values;
    session.done = reply.done;
    bubble(reply.message);
    if (reply.result) {
      session.projectId = reply.persistence?.world_id ? reply.persistence.project_id : null;
      const preview = $('wizard-preview');
      preview.replaceChildren(element('h3', {}, reply.result.world.title), prose(reply.result.world.summary),
        reply.warning ? muted(reply.warning) : null, reply.persistence?.error ? muted(reply.persistence.error) : null,
        session.projectId ? pill('世界观已保存') : muted('世界观未完整入库。'));
      preview.hidden = false;
    }
  } catch (error) {
    bubble(error.status === 404 ? '这个会话已过期，请点击「开始新对话」。' : `请求失败：${error.message}`);
  } finally { session.busy = false; syncWizard(); }
}

$('new-project').onclick = openWizard;
$('refresh').onclick = () => loadLibrary();
$('close-wizard').onclick = () => { if (!session.busy) wizard.close(); };
wizard.addEventListener('cancel', (event) => { if (session.busy) event.preventDefault(); });
$('restart-wizard').onclick = startConversation;
$('open-created').onclick = () => { wizard.close(); void loadLibrary(session.projectId); };
$('confirm-world').onclick = () => sendMessage('确认生成');
$('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const input = $('chat-input');
  const text = input.value.trim();
  if (!text) return;
  input.value = '';
  void sendMessage(text);
});

function showAccessDialog() {
  connection('等待进入', 'waiting');
  if (!$('access-dialog').open) $('access-dialog').showModal();
}

$('access-dialog').addEventListener('cancel', (event) => event.preventDefault());
$('access-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  accessKey = $('access-key').value;
  $('access-submit').disabled = true;
  $('access-error').hidden = true;
  try {
    await api('/access');
    try { sessionStorage.setItem('mojian-access-key', accessKey); } catch { /* optional */ }
    $('access-key').value = '';
    $('access-dialog').close();
    notify();
    await loadLibrary();
  } catch (error) {
    $('access-error').textContent = error.status === 401 ? '口令不正确，请重新输入。' : error.message;
    $('access-error').hidden = false;
  } finally {
    $('access-submit').disabled = false;
  }
});

async function startWorkbench() {
  try {
    await api('/access');
    await loadLibrary();
  } catch (error) {
    if (error.status !== 401) {
      connection('暂未连接', 'error');
      notify('暂时无法连接创作空间，请确认服务器已启动后刷新。', 'error');
    }
  }
}

void startWorkbench();
