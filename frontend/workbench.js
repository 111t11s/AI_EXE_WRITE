import {authenticatedFetch, getAuthToken, resolveApiBaseUrl, setAuthToken} from './api-client.js';
import {createGenerationView, watchGeneration} from './generation-stream.js';

const API_BASE_URL = resolveApiBaseUrl(window.AITXT_CONFIG?.apiBaseUrl);
const $ = (id) => document.getElementById(id);
const statusNames = {planning: '规划中', writing: '写作中', completed: '已完成', paused: '暂停',
  planned: '待写作', draft: '草稿', reviewed: '已审读', published: '已发布',
  approved: '已确认', active: '使用中', archived: '已归档'};
const labels = {genre: '小说类型', setting: '时代与背景', power_system: '力量体系', cheat: '金手指',
  protagonist: '主角设定', goal: '主角目标', tone: '故事基调', length: '篇幅规划'};
const pageSize = 15;
const initialState = () => ({projects: [], project: null, worlds: [], outlines: [], tab: 'overview',
  outline: null, volumes: null, volume: null, beats: null, page: null, chapter: null, chapterId: null,
  characters: null, characterError: null, characterLoading: false, characterOutlineId: null,
  consistencyPage: null, consistencyLoading: false, consistencyError: null, consistencySelection: new Map(),
  consistencyOutlineId: null, consistencyReport: null,
  taskCategory: 'all', taskStatus: 'running', taskMenuOpen: false, tasksLoaded: false, tasksError: null,
  busy: false, screen: 'project', reviews: new Map()});
const state = initialState();
let account = null;
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
  const requestingToken = getAuthToken();
  const query = new URLSearchParams(filters ?? {}).toString();
  const response = await authenticatedFetch(`${API_BASE_URL}${path}${query ? `?${query}` : ''}`, {
    method, cache: 'no-store',
    ...(data !== undefined ? {
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(data),
    } : {}),
  });
  const body = await response.json().catch(() => null);
  if (getAuthToken() !== requestingToken) {
    const error = new Error('登录状态已变更，请重新操作。');
    error.status = 401;
    throw error;
  }
  if (!response.ok) {
    const detail = body?.detail;
    const message = Array.isArray(detail) ? detail.map((item) => item.msg).join('；') : detail;
    const error = new Error(typeof message === 'string' ? message : `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  return body;
}

const post = (path, data = {}) => api(path, {method: 'POST', data});

const progressGeneration = createGenerationView($('task-progress-stream'));
const jobStatuses = {running: '生成中', completed: '已完成', failed: '未完成'};
const taskCategories = [['all', '全部任务'], ['worldbuilding', '世界观'], ['outline', '大纲'], ['draft', '正文'],
  ['characters', '角色'], ['plan', '章节规划'], ['review', '审读'], ['consistency', '一致性核对'], ['other', '其他']];
const knownTaskKinds = new Set(taskCategories.filter(([kind]) => !['all', 'other'].includes(kind)).map(([kind]) => kind));
const taskCategoryName = kind => taskCategories.find(([value]) => value === kind)?.[1] || '其他';
const taskInCategory = (job, category) => category === 'all' || (category === 'other' ? !knownTaskKinds.has(job.kind) : job.kind === category);
const sortedJobs = items => [...items].sort((left, right) =>
  Number(right.status === 'running') - Number(left.status === 'running') ||
  (Date.parse(right.created_at) || 0) - (Date.parse(left.created_at) || 0) || String(right.id).localeCompare(String(left.id)));
let jobList = [];
let jobsTimer = null;
let jobsLoading = false;
let progressId = null;
let progressAbort = null;

function stopProgress() {
  progressAbort?.abort();
  progressAbort = null;
  progressId = null;
}

function jobRow(job) {
  const project = state.projects.find(item => item.id === job.project_id);
  return element('div', {class: 'task-row', 'data-job-id': job.id},
    element('div', {}, element('strong', {}, job.title),
      element('p', {class: 'muted'}, `${project?.title || (job.kind === 'worldbuilding' ? '新小说' : '小说任务')} · ${taskCategoryName(job.kind)} · ${jobStatuses[job.status] || job.status} · ${job.message}`),
      element('p', {class: 'muted'}, `发起时间：${new Date(job.created_at).toLocaleString()}`)),
    button('查看进度', () => openProgress(job.id)));
}

function renderTaskMenu() {
  const expanded = state.taskMenuOpen;
  $('generation-tasks').setAttribute('aria-expanded', String(expanded));
  $('generation-tasks').setAttribute('aria-current', ['tasks', 'progress'].includes(state.screen) ? 'page' : 'false');
  const menu = $('task-submenus');
  menu.hidden = !expanded;
  menu.replaceChildren(...taskCategories.filter(([kind]) => kind !== 'other' || jobList.some(job => !knownTaskKinds.has(job.kind)))
    .map(([kind, title]) => {
      const jobs = jobList.filter(job => taskInCategory(job, kind));
      const running = jobs.filter(job => job.status === 'running').length;
      return element('button', {type: 'button', class: 'task-menu-item', 'data-task-category': kind,
        'aria-current': ['tasks', 'progress'].includes(state.screen) && state.taskCategory === kind ? 'page' : null,
        click: () => openTasks(kind)}, element('span', {}, title),
      element('span', {class: 'task-menu-count', 'aria-label': `${running} 个生成中，共 ${jobs.length} 项任务`}, String(running)));
    }));
}

function renderJobSummary() {
  const active = jobList.filter(job => job.status === 'running').length;
  $('generation-task-count').textContent = `${active} 进行中`;
  renderTaskMenu();
  const summary = $('task-summary');
  summary.hidden = !jobList.length || state.screen !== 'project';
  summary.replaceChildren(...sortedJobs(jobList).slice(0, 3).map(jobRow));
}

async function loadJobs(quiet = true) {
  if (!account || jobsLoading) return;
  const requestingAccount = account;
  const request = key('jobs');
  jobsLoading = true;
  try {
    const page = await api('/generation-jobs');
    if (account !== requestingAccount || !current('jobs', request)) return;
    const changed = !state.tasksLoaded || state.tasksError || JSON.stringify(jobList) !== JSON.stringify(page.items);
    jobList = page.items;
    state.tasksLoaded = true;
    if (state.tasksError && state.screen === 'tasks') notify();
    state.tasksError = null;
    renderJobSummary();
    if (state.screen === 'tasks' && changed) render();
  } catch (error) {
    if (account === requestingAccount) {
      state.tasksError = error.message;
      if (state.screen === 'tasks') render();
      if (!quiet) notify(`任务列表读取失败：${error.message}`, 'error');
    }
  } finally { if (account === requestingAccount) jobsLoading = false; }
}

function recordJob(job) {
  key('jobs');
  jobList = [job, ...jobList.filter(item => item.id !== job.id)];
  renderJobSummary();
  if (state.screen === 'tasks') render();
  notify('任务已在后台开始，可点击「查看进度」查看实时生成内容。');
}

function openTasks(category = state.taskCategory) {
  stopProgress();
  state.taskCategory = category;
  state.taskMenuOpen = true;
  state.screen = 'tasks';
  render();
  void loadJobs(false);
}

function taskCenterView() {
  const category = state.taskCategory;
  const jobs = sortedJobs(jobList.filter(job => taskInCategory(job, category)));
  const filtered = jobs.filter(job => state.taskStatus === 'all' || job.status === state.taskStatus);
  const statuses = [['running', '生成中'], ['all', '全部状态'], ['completed', '已完成'], ['failed', '未完成']];
  const filters = element('nav', {class: 'task-status-filters', 'aria-label': '任务状态'}, statuses.map(([status, title]) =>
    element('button', {type: 'button', class: 'button secondary', 'data-task-status': status,
      'aria-pressed': String(state.taskStatus === status), click: () => { state.taskStatus = status; render(); }},
    `${title}（${jobs.filter(job => status === 'all' || job.status === status).length}）`)));
  const emptyMessage = !state.tasksLoaded && !state.tasksError ? '正在读取任务…' :
    state.tasksError ? '暂时无法读取任务，请点击刷新任务重试。' :
    state.taskStatus === 'running' ? '此分类暂无正在生成的任务，可切换「全部状态」查看历史任务。' : '此分类暂无符合条件的任务。';
  return panel(`任务中心 · ${taskCategoryName(category)}`, stack(
    muted('汇总当前账号所有小说的后台任务，每 4 秒自动更新；点击查看进度后可查看实时内容和生成结果。'),
    filters,
    state.tasksError ? prose(`任务刷新失败：${state.tasksError}。下方保留上次读取的任务。`) : null,
    ...(filtered.length ? filtered.map(jobRow) : [muted(emptyMessage)])
  ), `共 ${jobs.length} 项任务，${jobs.filter(job => job.status === 'running').length} 项正在生成。`,
  button('刷新任务', () => loadJobs(false)));
}

function renderTaskResult(job, result) {
  const content = [];
  if (job.kind === 'review') content.push(reviewView(result));
  else if (job.kind === 'consistency') content.push(consistencyReportView(result));
  else {
    let message = '生成结果已保存，可打开小说查看。';
    if (job.kind === 'worldbuilding') message = result.persistence?.world_id ? '世界观已保存。' : '生成已完成，请核对保存状态。';
    if (job.kind === 'outline') message = `大纲 v${result.version_no} 已保存，共 ${result.volume_ids.length} 卷。`;
    if (job.kind === 'plan') message = `已保存 ${result.chapters.length} 个计划章节。`;
    if (job.kind === 'draft') message = `正文已保存，共 ${result.word_count} 字。`;
    if (job.kind === 'characters') message = `已保存 ${result.character_ids.length} 位角色：${[
      ['protagonist', '主角'], ['important', '重要配角'], ['supporting', '配角']
    ].filter(([kind]) => result.counts[kind]).map(([kind, label]) => `${label} ${result.counts[kind]} 位`).join('、')}。`;
    content.push(panel('生成结果', prose(message)));
  }
  if (result.warning || result.persistence?.error) content.push(prose(result.warning || result.persistence.error));
  const projectId = job.project_id || result.project_id || result.persistence?.project_id;
  if (projectId) content.push(actions(button('打开小说查看结果', async () => {
    const requestingAccount = account;
    stopProgress();
    state.screen = 'project';
    await loadLibrary(projectId);
    if (account !== requestingAccount || state.project?.id !== projectId) return;
    if (job.kind === 'consistency') {
      state.tab = 'consistency';
      state.consistencyReport = result;
      state.consistencyOutlineId = result.outline_id;
      await loadConsistencyChapters();
    } else if (job.kind === 'characters') {
      state.tab = 'characters';
      await loadCharacters();
    } else if (job.kind === 'outline') {
      state.tab = 'outline';
      await loadOutline(result.outline_id);
    } else if (['plan', 'draft', 'review'].includes(job.kind)) {
      state.tab = 'chapters';
      if (job.kind === 'review') state.reviews.set(job.resource_id, result);
      await loadChapters(0, result.chapters?.[0]?.chapter_id || job.resource_id);
    }
    render();
  }, 'primary')));
  $('task-progress-result').replaceChildren(...content);
}

async function openProgress(id) {
  stopProgress();
  const controller = new AbortController();
  progressAbort = controller;
  progressId = id;
  const requestingAccount = account;
  state.screen = 'progress';
  let job = jobList.find(item => item.id === id) || {id};
  $('task-progress-title').textContent = job.title || '正在打开任务…';
  $('task-progress-description').textContent = '正在连接进度…';
  $('task-progress-result').replaceChildren();
  $('reconnect-progress').hidden = true;
  render();
  progressGeneration.begin();
  try {
    const result = await watchGeneration(`${API_BASE_URL}/generation-jobs/${id}/events`, (event, data) => {
      if (progressId !== id || progressAbort !== controller || account !== requestingAccount) return;
      if (event === 'snapshot') {
        job = data;
        $('task-progress-title').textContent = data.title;
        $('task-progress-description').textContent = `${jobStatuses[data.status]} · ${new Date(data.created_at).toLocaleString()}`;
      }
      progressGeneration.event(event, data);
    }, {signal: controller.signal});
    if (progressAbort !== controller || account !== requestingAccount) return;
    progressGeneration.finish(result.warning || '生成完成');
    $('task-progress-description').textContent = '已完成';
    renderTaskResult(job, result);
    void loadJobs();
  } catch (error) {
    if (controller.signal.aborted || progressAbort !== controller || account !== requestingAccount) return;
    progressGeneration.fail(error.message);
    $('task-progress-description').textContent = '任务未完成或进度连接中断，请查看提示。';
    $('reconnect-progress').hidden = false;
    void loadJobs();
  }
}

$('generation-tasks').onclick = () => openTasks('all');
$('back-to-tasks').onclick = () => openTasks();
$('reconnect-progress').onclick = () => { if (progressId) void openProgress(progressId); };

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
  $('account-logout').disabled = state.busy;
  $('account-password-change').disabled = state.busy;
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
  try { return Number(localStorage.getItem(`mojian-project-${account.id}`)); } catch { return null; }
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
  stopProgress();
  state.screen = 'project';
  const token = key('project');
  for (const name of ['outline', 'volume', 'chapters', 'chapter', 'characters', 'consistency']) key(name);
  Object.assign(state, {project: state.projects.find((item) => item.id === projectId) ?? null, worlds: [], outlines: [],
    tab: 'overview', outline: null, volumes: null, volume: null, beats: null, page: null,
    chapter: null, chapterId: null, chapterLoading: false, chapterListError: null, outlineError: null,
    volumeError: null, projectLoading: true, projectError: null,
    characters: null, characterError: null, characterLoading: false, characterOutlineId: null,
    consistencyPage: null, consistencyLoading: false, consistencyError: null, consistencySelection: new Map(),
    consistencyOutlineId: null, consistencyReport: null});
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
    try { localStorage.setItem(`mojian-project-${account.id}`, String(projectId)); } catch { /* optional */ }
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
    ['角色', '先生成主角与重要配角，有大纲后再补充配角'],
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
  view.hidden = state.screen === 'progress';
  $('task-progress-page').hidden = state.screen !== 'progress';
  renderJobSummary();
  if (state.screen === 'progress') return;
  if (state.screen === 'tasks') {
    view.replaceChildren(taskCenterView());
    return;
  }
  view.setAttribute('aria-busy', String(Boolean(state.projectLoading)));
  if (!state.project) {
    view.replaceChildren(empty(state.libraryError ? '暂时打不开书架' : '开始一本新小说',
      state.libraryError ? '连接恢复后刷新，即可继续上次的创作。' : '先和世界观助手聊聊，也可以从左侧打开已有项目。',
      state.libraryError ? button('刷新书架', () => loadLibrary(), 'primary') : button('新建小说', openWizard, 'primary')));
    return;
  }
  const tabs = element('nav', {class: 'tabs', 'aria-label': '项目页面'},
    [['overview', '概览'], ['characters', '角色'], ['outline', '大纲与分卷'], ['chapters', '章节与正文'], ['consistency', '一致性核对']].map(([tab, title]) =>
      element('button', {type: 'button', class: 'tab', 'aria-current': state.tab === tab ? 'page' : null,
        click: () => activate(tab)}, title)));
  let content;
  if (state.projectLoading) content = empty('正在读取项目', '正在加载世界观和大纲。');
  else if (state.projectError) content = empty('项目读取失败', state.projectError,
    button('重试', () => openProject(state.project.id)));
  else if (state.tab === 'outline') content = outlineView();
  else if (state.tab === 'chapters') content = chapterView();
  else if (state.tab === 'characters') content = characterView();
  else if (state.tab === 'consistency') content = consistencyView();
  else content = overview();
  view.replaceChildren(hero(state.project), tabs, content);
}

async function activate(tab) {
  state.tab = tab;
  render();
  if (state.projectLoading || state.projectError) return;
  if (tab === 'outline' && !state.outline && state.outlines[0]) await loadOutline(state.outlines[0].id);
  if (tab === 'chapters' && !state.page && !state.chapterLoading) await loadChapters();
  if (tab === 'characters' && state.characters === null && !state.characterLoading) await loadCharacters();
  if (tab === 'consistency' && !state.consistencyPage && !state.consistencyLoading) await loadConsistencyChapters();
}

async function loadConsistencyChapters(offset = 0) {
  const token = key('consistency');
  state.consistencyLoading = true;
  state.consistencyError = null;
  render();
  try {
    const page = await api('/chapters', {filters: {project_id: state.project.id, limit: pageSize, offset}});
    if (current('consistency', token)) state.consistencyPage = page;
  } catch (error) {
    if (current('consistency', token)) state.consistencyError = error.message;
  } finally {
    if (current('consistency', token)) { state.consistencyLoading = false; render(); }
  }
}

function selectConsistencyChapter(row, selected) {
  if (selected && state.consistencySelection.size >= 20 && !state.consistencySelection.has(row.id)) {
    notify('每次最多核对 20 章，请分批选择。', 'error');
  } else if (selected) state.consistencySelection.set(row.id, {id: row.id, chapter_number: row.chapter_number, title: row.title});
  else state.consistencySelection.delete(row.id);
  render();
}

function consistencyView() {
  const page = state.consistencyPage;
  const selected = [...state.consistencySelection.values()].sort((a, b) => a.chapter_number - b.chapter_number);
  const outlineId = state.consistencyOutlineId || state.outlines[0]?.id;
  const version = element('select', {'aria-label': '一致性核对大纲版本', disabled: !state.outlines.length,
    change: (event) => { state.consistencyOutlineId = Number(event.target.value); }},
    state.outlines.map(row => element('option', {value: row.id, selected: row.id === outlineId}, `大纲 v${row.version_no}`)));
  const controls = panel('一致性核对智能体', stack(
    muted('多选章节，结合指定大纲、人物档案和世界规则检查情节与人物是否偏移。仅在点击开始后运行，报告包含章节原文、对照依据和修改建议。'),
    actions(element('label', {class: 'control'}, '对照大纲', version),
      button(`核对所选章节（${selected.length}）`, consistencyDialog, 'primary', state.busy || !selected.length || !outlineId || state.worlds.length !== 1),
      button('清空选择', () => { state.consistencySelection.clear(); render(); }, 'secondary', !selected.length),
      button('刷新章节', () => loadConsistencyChapters(page?.offset || 0), 'secondary', state.consistencyLoading)),
    muted('最多选择 20 章、正文合计 120000 字符；可跨页勾选，翻页保留选择。只核对所选范围，报告基于读取时的资料。'),
    selected.length ? muted(`已选：${selected.map(row => `第 ${row.chapter_number} 章`).join('、')}`) : null,
    !outlineId ? muted('请先生成大纲。') : null,
    state.worlds.length !== 1 ? muted('请先设置唯一的当前世界观。') : null));
  let directory;
  if (state.consistencyError) directory = empty('章节读取失败', state.consistencyError, button('重试', () => loadConsistencyChapters(page?.offset || 0)));
  else if (!page) directory = muted('正在读取章节…');
  else directory = panel('选择核对章节', stack(
    actions(button('勾选本页有正文的章节', () => {
      const candidates = page.items.filter(row => row.content.trim());
      const count = new Set([...state.consistencySelection.keys(), ...candidates.map(row => row.id)]).size;
      if (count > 20) { notify('勾选后将超过 20 章，请逐章选择或先清空选择。', 'error'); return; }
      for (const row of candidates) state.consistencySelection.set(row.id, {id: row.id, chapter_number: row.chapter_number, title: row.title});
      render();
    }, 'secondary', state.consistencyLoading || !page.items.some(row => row.content.trim()))),
    ...page.items.map(row => element('label', {class: 'consistency-chapter'},
      element('input', {type: 'checkbox', checked: state.consistencySelection.has(row.id), disabled: !row.content.trim() || state.consistencyLoading,
        'data-chapter-id': row.id, change: (event) => selectConsistencyChapter(row, event.target.checked)}),
      element('span', {}, element('strong', {}, `第 ${row.chapter_number} 章 · ${row.title || '未命名'}`),
        muted(row.content.trim() ? `${statusNames[row.status] || row.status} · ${row.word_count} 字` : '尚无正文，暂不可核对')))),
    !page.items.length ? muted('还没有章节，请先规划章节并完成正文。') : null,
    page.offset || page.has_more ? element('div', {class: 'paging'},
      button('上一页', () => loadConsistencyChapters(page.offset - pageSize), 'secondary', !page.offset || state.consistencyLoading),
      `第 ${Math.floor(page.offset / pageSize) + 1} 页`,
      button('下一页', () => loadConsistencyChapters(page.offset + pageSize), 'secondary', !page.has_more || state.consistencyLoading)) : null));
  return stack(controls, directory, state.consistencyReport ? consistencyReportView(state.consistencyReport) : null);
}

function consistencyReportView(report) {
  const verdicts = {consistent: '所选范围未发现明确偏移', drifted: '发现偏移，建议修正', insufficient: '资料不足，暂无法完整判断'};
  const categories = {plot: '剧情偏移', character: '人物偏移', world: '设定冲突', continuity: '章节衔接'};
  const kinds = {outline: '大纲', volume: '分卷', plot_beat: '剧情节点', character: '人物档案', world: '世界观', chapter: '章节'};
  const fieldLabels = {content: '正文', summary: '概要', personality: '性格', background: '背景', goal: '目标', motivation: '动机',
    weakness: '弱点', character_arc: '成长弧线', abilities: '能力', rules: '规则', history: '历史', cultures: '文化',
    central_conflict: '核心冲突', themes: '主题', protagonist_goal: '主角目标', main_conflict: '主要冲突', climax: '高潮', ending_hook: '结尾悬念', foreshadowing: '伏笔'};
  return panel('一致性核对报告', stack(prose(report.summary),
    muted(`对照大纲 v${report.outline_version} · 读取时间：${new Date(report.checked_at).toLocaleString()}`),
    muted(`核对范围：${report.chapters.map(row => `第 ${row.chapter_number} 章 · ${row.title}`).join('、')}`),
    ...report.issues.map(issue => {
      const reference = report.references.find(row => row.kind === issue.basis.kind && row.record_id === issue.basis.record_id);
      return element('section', {class: 'report-issue'}, actions(pill(categories[issue.category]), pill(issue.severity === 'major' ? '主要问题' : '细节建议', true)),
        element('h3', {}, issue.title),
        ...issue.evidence.map(evidence => {
          const chapter = report.chapters.find(row => row.chapter_id === evidence.chapter_id);
          return stack(muted(`章节依据 · 第 ${chapter.chapter_number} 章 · ${chapter.title}`), element('blockquote', {class: 'prose consistency-quote'}, evidence.quote));
        }),
        muted(`对照依据 · ${kinds[issue.basis.kind]} · ${reference?.label || ''} · ${fieldLabels[issue.basis.field] || issue.basis.field}`),
        element('blockquote', {class: 'prose consistency-quote'}, issue.basis.quote),
        prose(`偏移说明：${issue.explanation}`), prose(`修改建议：${issue.suggestion}`));
    }),
    report.limitations.length ? details(['核对范围与限制', report.limitations.join('\n')]) : null),
    '报告可在任务中心再次查看，记录保留规则与其他后台任务相同。', pill(verdicts[report.verdict], report.verdict !== 'consistent'));
}

async function loadCharacters() {
  const token = key('characters');
  const projectId = state.project.id;
  state.characterLoading = true;
  state.characterError = null;
  render();
  try {
    const rows = await allRows('/characters', {project_id: projectId});
    if (current('characters', token)) state.characters = rows;
  } catch (error) {
    if (current('characters', token)) state.characterError = error.message;
  } finally {
    if (current('characters', token)) { state.characterLoading = false; render(); }
  }
}

function characterView() {
  const outlineId = state.characterOutlineId || state.outlines[0]?.id;
  const version = element('select', {'aria-label': '配角生成使用的大纲版本',
    change: (event) => { state.characterOutlineId = Number(event.target.value); }},
    state.outlines.map(row => element('option', {value: row.id, selected: row.id === outlineId}, `大纲 v${row.version_no}`)));
  const controls = panel('角色生成智能体', stack(
    muted('根据当前世界观建立主角与重要配角；大纲完成后，再补充推动剧情的其他配角。任务在后台生成，可通过「查看进度」查看实时内容。'),
    actions(button('生成主角与重要配角', coreCharacterDialog, 'primary', state.busy || state.worlds.length !== 1),
      state.outlines.length ? version : null,
      button('根据大纲补充配角', supportingCharacterDialog, 'secondary', state.busy || !state.outlines.length || state.worlds.length !== 1),
      button('刷新角色', loadCharacters, 'secondary', state.characterLoading)),
    muted('可设置生成数量和补充要求。新增角色保存在当前小说下，已有角色会作为创作依据；同名冲突时整批不保存。'),
    state.worlds.length !== 1 ? muted('请先设置唯一的当前世界观。') : null,
    !state.outlines.length ? muted('生成大纲后即可补充剧情配角。') : null));
  if (state.characterError) return stack(controls, empty('角色读取失败', state.characterError, button('重试', loadCharacters)));
  if (state.characters === null) return stack(controls, muted('正在读取角色…'));
  if (!state.characters.length) return stack(controls, empty('尚未建立角色', '先生成主角与重要配角，为后续大纲和正文提供人物设定。'));
  return stack(controls, panel(`角色档案 · ${state.characters.length} 位`, stack(...state.characters.map(row =>
    element('details', {class: 'character-card'}, element('summary', {}, element('strong', {}, row.name), ' ', pill(row.role)),
      details(['简介与剧情作用', row.summary], ['性格', row.personality], ['背景经历', row.background],
        ['目标', row.goal], ['动机', row.motivation], ['弱点', row.weakness], ['成长弧线', row.character_arc],
        ['能力', row.abilities], ['首次出场章节', row.first_appearance_chapter ? String(row.first_appearance_chapter) : null]))))));
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
  '审读建议不会改动正文，可从任务中心再次查看。', pill(report.verdict === 'ready' ? '可以继续' : '建议修改', report.verdict !== 'ready'));
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
  $('action-submit').textContent = busy ? (task?.pending || '正在生成…') : (task?.submit || '继续');
}

function closeAction() { if (!state.busy) action.close(); }
$('close-action').onclick = closeAction;
$('cancel-action').onclick = closeAction;
action.addEventListener('cancel', (event) => { if (state.busy) event.preventDefault(); });

$('action-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (state.busy || !task) return;
  const requestingAccount = account;
  const submitted = task;
  const data = Object.fromEntries(new FormData(event.currentTarget));
  for (const field of submitted.fields) {
    if (field.type === 'number') data[field.name] = Number(data[field.name]);
    else if (field.name !== 'content') data[field.name] = data[field.name].trim();
  }
  $('action-error').hidden = true;
  setBusy(submitted.taskPath ? '正在提交后台任务…' : (submitted.pending || '正在保存…'));
  setActionBusy(true);
  try {
    let receipt;
    try {
      receipt = submitted.taskPath ? await post(submitted.taskPath, submitted.payload ? submitted.payload(data) : data) : await submitted.send(data);
      if (account !== requestingAccount) return;
    }
    catch (error) {
      if (account !== requestingAccount) return;
      $('action-error').textContent = error instanceof TypeError ? '连接中断。若操作会写入数据，请刷新项目核对结果后再试。' : error.message;
      $('action-error').hidden = false;
      return;
    }
    action.close();
    if (submitted.taskPath) {
      recordJob(receipt);
      return;
    }
    notify(submitted.success(receipt));
    try { await submitted.after(receipt); }
    catch (error) { notify(`操作已完成，但刷新结果失败：${error.message}。请刷新书架。`, 'error'); }
  } finally {
    if (account === requestingAccount) {
      setActionBusy(false);
      setBusy();
      render();
    }
  }
});

async function outlineDialog() {
  const id = state.project.id;
  const requestingAccount = account;
  let selection;
  try { selection = await api('/novel-assistant/outline-skills', {filters: {genre: state.project.genre || ''}}); }
  catch (error) { if (account === requestingAccount) notify(`大纲技能加载失败：${error.message}`, 'error'); return; }
  if (account !== requestingAccount || state.project?.id !== id) return;
  openAction({title: '生成新大纲', description: `根据当前世界观创建一个新版本，已有大纲会保留。${selection.notice}。`,
    submit: '生成并保存', fields: [numberField('volume_count', '分卷数', 3, 1, 5), noteField],
    taskPath: `/writing/projects/${id}/outline/tasks`,
  });
}

function coreCharacterDialog() {
  openAction({title: '生成主角与重要配角', description: '根据当前世界观、力量规则、势力和已有角色创作核心人物。数量表示本次新增人数。',
    submit: '后台生成并保存', fields: [numberField('protagonist_count', '新增主角数', 1, 1, 2),
      numberField('important_count', '新增重要配角数', 3, 0, 8), noteField],
    taskPath: `/writing/projects/${state.project.id}/characters/core/tasks`});
}

function supportingCharacterDialog() {
  const outlineId = state.characterOutlineId || state.outlines[0]?.id;
  if (!outlineId) return;
  const outline = state.outlines.find(row => row.id === outlineId);
  openAction({title: '根据大纲补充配角', description: `使用大纲 v${outline.version_no} 及其分卷、剧情节点补充其他配角，保留已有主角和重要配角。`,
    submit: '后台生成并保存', fields: [numberField('supporting_count', '新增配角数', 6, 1, 12), noteField],
    taskPath: `/writing/outlines/${outlineId}/characters/supporting/tasks`});
}

function planDialog(volume) {
  const page = state.page;
  const start = page && !page.has_more ? Math.min((page.items.at(-1)?.chapter_number ?? 0) + 1, 4294967295) : 1;
  openAction({title: '规划章节', description: `把《${volume.title}》的剧情节点拆成章节。章号在全书内唯一，已有章节不会被覆盖。`,
    submit: '规划并保存', fields: [numberField('start_chapter', '起始章号', start, 1, 4294967295),
      numberField('chapter_count', '生成章节数', 5, 1, 12), noteField],
    taskPath: `/writing/volumes/${volume.id}/chapter-plan/tasks`,
  });
}

function draftDialog(chapter) {
  openAction({title: '生成正文', description: `为第 ${chapter.chapter_number} 章生成初稿，正文会直接保存到当前章节。`,
    submit: '写作并保存', fields: [numberField('target_characters', '目标篇幅（字符）', 2000, 300, 6000), noteField],
    taskPath: `/writing/chapters/${chapter.id}/draft/tasks`,
  });
}

function reviewDialog(chapter) {
  openAction({title: '审读本章', description: '检查世界规则、人物动机、前文衔接和章节目标。审读只给建议，不会修改正文。',
    submit: '开始审读', fields: [noteField], taskPath: `/writing/chapters/${chapter.id}/review/tasks`,
  });
}

function consistencyDialog() {
  const chapters = [...state.consistencySelection.values()].sort((a, b) => a.chapter_number - b.chapter_number);
  const outlineId = state.consistencyOutlineId || state.outlines[0]?.id;
  if (!chapters.length || !outlineId) return;
  const outline = state.outlines.find(row => row.id === outlineId);
  openAction({title: '开始一致性核对', description: `对照大纲 v${outline.version_no} 核对 ${chapters.map(row => `第 ${row.chapter_number} 章`).join('、')}，报告会给出偏移依据与修改建议。`,
    submit: '后台开始核对', fields: [{...noteField, placeholder: '例如：重点核对主角动机、能力边界和大纲推进方向'}],
    taskPath: `/writing/projects/${state.project.id}/consistency/tasks`,
    payload: data => ({...data, outline_id: outlineId, chapter_ids: chapters.map(row => row.id)})});
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
let session = {id: null, values: {}, question: null, custom: false, done: false, busy: false};

function syncWizard() {
  const {id, values, question, custom, done, busy} = session;
  const count = Object.keys(labels).filter((name) => values[name]).length;
  $('slot-count').textContent = `${count} / ${Object.keys(labels).length}`;
  $('slots').replaceChildren(...Object.entries(labels).map(([name, label]) =>
    element('div', {}, element('dt', {}, label), element('dd', {}, values[name] || '待补充'))));
  $('outline-skill').textContent = session.outlineSkill?.notice || '选择小说类型后自动加载大纲设计技能。';
  $('chat-input').disabled = !id || busy || done;
  $('chat-send').disabled = !id || busy || done;
  $('chat-form').hidden = !id || done || Boolean(question && !custom);
  $('chat-input').placeholder = question ? `输入自定义${question.label}…` : '修改设定，例如：小说类型：仙侠';
  $('question-picker').hidden = !id || !question || done;
  $('question-options').replaceChildren();
  if (question && !done) {
    $('question-label').textContent = `${question.label} · 点击选择，或自定义填写`;
    $('question-options').append(...question.options.map((option) =>
      button(option, () => sendMessage(option), 'secondary question-option', busy)),
    element('button', {type: 'button', class: 'button secondary question-option custom-option',
      disabled: busy, 'aria-pressed': String(custom), 'aria-controls': 'chat-form', click: () => {
        session.custom = !session.custom;
        syncWizard();
        if (session.custom) $('chat-input').focus();
      }}, '自定义…'));
  }
  $('confirm-world').disabled = !id || busy || done || count !== Object.keys(labels).length;
  $('confirm-world').textContent = busy && count === Object.keys(labels).length ? '处理中…' : '确认并生成';
  $('restart-wizard').disabled = busy;
  $('close-wizard').disabled = busy;
}

function bubble(text, user = false) {
  $('chat').append(element('p', {class: `bubble${user ? ' user' : ''}`}, text));
  $('chat').scrollTop = $('chat').scrollHeight;
}

async function startConversation() {
  session = {id: null, values: {}, question: null, custom: false, done: false, busy: true};
  const requestingSession = session;
  $('chat').replaceChildren();
  $('chat-input').value = '';
  syncWizard();
  try {
    const reply = await post('/novel-assistant/sessions');
    if (session !== requestingSession) return;
    session.id = reply.session_id;
    session.values = reply.values;
    session.question = reply.question;
    session.outlineSkill = reply.outline_skill;
    bubble(reply.message);
  } catch (error) {
    if (session === requestingSession) bubble(`无法开始对话：${error.message}。可点击「开始新对话」重试。`);
  } finally {
    if (session === requestingSession) { session.busy = false; syncWizard(); }
  }
}

function openWizard() {
  if (state.busy) return;
  wizard.showModal();
  if (!session.id || session.done) void startConversation();
  else syncWizard();
}

async function sendMessage(content) {
  if (!content || !session.id || session.busy || session.done) return;
  const requestingSession = session;
  session.busy = true;
  syncWizard();
  bubble(content, true);
  const generating = ['确认生成', '确认', '开始生成'].includes(content);
  try {
    const path = `/novel-assistant/sessions/${session.id}/messages`;
    const reply = await post(generating ? `${path}/tasks` : path, {content});
    if (session !== requestingSession) return;
    if (generating) {
      session.done = true;
      session.question = null;
      wizard.close();
      recordJob(reply);
      return;
    }
    session.values = reply.values;
    session.done = reply.done;
    session.question = reply.question;
    session.outlineSkill = reply.outline_skill;
    session.custom = false;
    $('chat-input').value = '';
    bubble(reply.message);
  } catch (error) {
    if (session !== requestingSession) return;
    bubble(error.status === 404 ? '这个会话已过期，请点击「开始新对话」。' : `请求失败：${error.message}`);
  } finally {
    if (session === requestingSession) { session.busy = false; syncWizard(); }
  }
}

$('new-project').onclick = openWizard;
$('refresh').onclick = () => loadLibrary();
$('close-wizard').onclick = () => { if (!session.busy) wizard.close(); };
wizard.addEventListener('cancel', (event) => { if (session.busy) event.preventDefault(); });
$('restart-wizard').onclick = startConversation;
$('confirm-world').onclick = () => sendMessage('确认生成');
$('chat-form').addEventListener('submit', (event) => {
  event.preventDefault();
  const input = $('chat-input');
  const text = input.value.trim();
  if (!text) return;
  void sendMessage(text);
});

const accountDialog = $('account-dialog');
const passwordDialog = $('password-dialog');
let registering = false;
let accountBusy = false;
let passwordBusy = false;

function showAccount(message = '') {
  $('app-shell').hidden = true;
  $('account-error').textContent = message;
  $('account-error').hidden = !message;
  if (!accountDialog.open) accountDialog.showModal();
}

function resetAccount(message = '') {
  stopProgress();
  clearInterval(jobsTimer);
  jobsTimer = null;
  jobsLoading = false;
  jobList = [];
  account = null;
  setAuthToken();
  setRegistrationMode(false);
  for (const name of Object.keys(sequence)) key(name);
  for (const name of Object.keys(state)) delete state[name];
  Object.assign(state, initialState());
  session = {id: null, values: {}, question: null, custom: false, done: false, busy: false};
  task = null;
  action.close();
  wizard.close();
  passwordDialog.close();
  $('password-form').reset();
  progressGeneration.hide();
  $('task-progress-result').replaceChildren();
  $('chat').replaceChildren();
  $('action-fields').replaceChildren();
  $('action-title').textContent = '';
  $('action-description').textContent = '';
  $('action-error').textContent = '';
  $('chat-input').value = '';
  notify();
  syncWizard();
  $('account-name').textContent = '';
  $('account-password').value = '';
  $('activity').hidden = true;
  renderLibrary();
  render();
  showAccount(message);
}

async function enterAccount(user) {
  account = user;
  $('account-name').textContent = user.display_name || user.username;
  $('account-password').value = '';
  accountDialog.close();
  $('app-shell').hidden = false;
  $('new-project').disabled = false;
  $('refresh').disabled = false;
  $('account-logout').disabled = false;
  $('account-password-change').disabled = false;
  notify();
  clearInterval(jobsTimer);
  jobsTimer = setInterval(() => { void loadJobs(); }, 4000);
  await Promise.all([loadLibrary(), loadJobs()]);
}

function setRegistrationMode(value) {
  registering = value;
  $('account-title').textContent = registering ? '注册账号' : '登录账号';
  $('account-submit').textContent = registering ? '注册并进入书架' : '登录';
  $('account-toggle').textContent = registering ? '已有账号，去登录' : '注册新账号';
  $('account-display-field').hidden = !registering;
  $('account-password').autocomplete = registering ? 'new-password' : 'current-password';
  $('account-error').hidden = true;
}

accountDialog.addEventListener('cancel', (event) => event.preventDefault());
$('account-toggle').onclick = () => setRegistrationMode(!registering);

$('account-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (accountBusy) return;
  accountBusy = true;
  const controls = [...event.currentTarget.elements];
  for (const control of controls) control.disabled = true;
  $('account-error').hidden = true;
  const data = {username: $('account-username').value.trim(), password: $('account-password').value};
  if (registering) data.display_name = $('account-display-name').value.trim();
  try {
    const receipt = await post(`/auth/${registering ? 'register' : 'login'}`, data);
    setAuthToken(receipt.access_token);
    await enterAccount(receipt.account);
  } catch (error) {
    $('account-error').textContent = error.message;
    $('account-error').hidden = false;
  } finally {
    accountBusy = false;
    for (const control of controls) control.disabled = false;
  }
});

$('account-logout').onclick = async () => {
  if (state.busy || session.busy) return;
  $('account-logout').disabled = true;
  try {
    await post('/auth/logout');
    resetAccount('已退出账号。');
  } catch (error) {
    if (error.status !== 401) notify(`退出失败：${error.message}`, 'error');
  } finally { $('account-logout').disabled = false; }
};

window.addEventListener('aitxt:unauthorized', () => resetAccount('登录已过期，请重新登录。'));

$('account-password-change').onclick = () => {
  if (!account || state.busy || session.busy || passwordBusy) return;
  $('password-error').hidden = true;
  $('password-form').reset();
  passwordDialog.showModal();
};
$('cancel-password').onclick = () => { if (!passwordBusy) passwordDialog.close(); };
passwordDialog.addEventListener('cancel', (event) => { if (passwordBusy) event.preventDefault(); });
passwordDialog.addEventListener('close', () => $('password-form').reset());
$('password-form').addEventListener('submit', async (event) => {
  event.preventDefault();
  if (passwordBusy) return;
  const data = {current_password: $('current-password').value, new_password: $('new-password').value};
  if (data.new_password !== $('confirm-password').value) {
    $('password-error').textContent = '两次输入的新密码不一致。';
    $('password-error').hidden = false;
    return;
  }
  const requestingAccount = account;
  const controls = [...event.currentTarget.elements];
  passwordBusy = true;
  for (const control of controls) control.disabled = true;
  $('password-error').hidden = true;
  try {
    await post('/auth/password', data);
    if (account === requestingAccount) resetAccount('密码已修改，请使用新密码重新登录。');
  } catch (error) {
    if (account === requestingAccount) {
      $('password-error').textContent = error.message;
      $('password-error').hidden = false;
    }
  } finally {
    passwordBusy = false;
    for (const control of controls) control.disabled = false;
  }
});

async function initializeAccount() {
  showAccount();
  if (!getAuthToken()) return;
  try { await enterAccount(await api('/auth/me')); }
  catch (error) { showAccount(error.status === 401 ? '请重新登录。' : error.message); }
}

export const ready = initializeAccount();
