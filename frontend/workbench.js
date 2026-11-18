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
const initialState = () => ({projects: [], project: null, libraryOwner: 'all', worlds: [], outlines: [], tab: 'overview',
  outline: null, volumes: null, volume: null, beats: null, page: null, chapter: null, chapterId: null,
  characters: null, characterProfiles: null, characterProgress: null, characterError: null, characterLoading: false, characterOutlineId: null,
  consistencyPage: null, consistencyLoading: false, consistencyError: null, consistencySelection: new Map(),
  consistencyOutlineId: null, consistencyReport: null,
  trash: null, trashLoading: false, trashError: null,
  taskCategory: 'all', taskProjectId: 'all', taskStatus: 'all', taskMenuOpen: false, tasksLoaded: false, tasksError: null,
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
const readOnlyProject = () => Boolean(account?.is_admin && state.project && state.project.account_id !== account.id);
const mutationButton = (...args) => readOnlyProject() ? null : button(...args);
const projectOwner = project => project.owner_username ?
  `${project.owner_display_name || project.owner_username}（${project.owner_username}）` :
  project.account_id ? `账号 #${project.account_id}` : '未分配账号';
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
const taskCategories = [['all', '全部任务'], ['worldbuilding', '世界观'], ['outline', '大纲'], ['characters', '角色'],
  ['draft', '正文'], ['plan', '章节规划'], ['review', '审读'], ['consistency', '一致性核对'], ['other', '其他']];
const knownTaskKinds = new Set(taskCategories.filter(([kind]) => !['all', 'other'].includes(kind)).map(([kind]) => kind));
const taskKindCategory = kind => kind === 'character_progress' ? 'characters' :
  kind === 'batch_draft' ? 'draft' : kind;
const taskCategoryName = kind => taskCategories.find(([value]) => value === taskKindCategory(kind))?.[1] || '其他';
const taskInCategory = (job, category) => category === 'all' || (category === 'other' ? !knownTaskKinds.has(taskKindCategory(job.kind)) : taskKindCategory(job.kind) === category);
const taskInProject = (job, projectId) => projectId === 'all' ||
  (projectId === 'unassigned' ? job.project_id == null : String(job.project_id) === String(projectId));
const taskProjectName = projectId => {
  if (projectId === 'all') return '全部书籍';
  if (projectId === 'unassigned') return '新小说';
  return state.projects.find(project => String(project.id) === String(projectId))?.title || '小说任务';
};
const taskProjectOptions = () => {
  const ids = new Set(jobList.map(job => job.project_id == null ? 'unassigned' : String(job.project_id)));
  return [['all', '全部书籍'], ...state.projects.filter(project => ids.has(String(project.id)))
    .map(project => [String(project.id), project.title || '未命名小说']),
  ...(ids.has('unassigned') ? [['unassigned', '新小说']] : [])];
};
const sortedJobs = items => [...items].sort((left, right) =>
  Number(right.status === 'running') - Number(left.status === 'running') ||
  (Date.parse(right.created_at) || 0) - (Date.parse(left.created_at) || 0) || String(right.id).localeCompare(String(left.id)));
let jobList = [];
let jobsTimer = null;
let jobsLoading = false;
let progressId = null;
let progressAbort = null;
let noticeTimer = null;

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
      element('span', {class: 'task-menu-count', 'aria-label': `${running} 个生成中，共 ${jobs.length} 项任务`}, String(jobs.length)));
    }));
}

function renderJobSummary() {
  const active = jobList.filter(job => job.status === 'running').length;
  $('generation-task-count').textContent = `${active} 进行中`;
  renderTaskMenu();
  const summary = $('task-summary');
  summary.hidden = true;
  summary.replaceChildren();
}

async function loadJobs(quiet = true) {
  if (!account || jobsLoading) return;
  const requestingAccount = account;
  const request = key('jobs');
  jobsLoading = true;
  try {
    const page = await api('/generation-jobs');
    if (account !== requestingAccount || !current('jobs', request)) return;
    const previous = new Map(jobList.map(job => [job.id, job]));
    const completed = state.tasksLoaded && page.items.some(job =>
      job.status === 'completed' && previous.get(job.id)?.status !== 'completed');
    for (const job of page.items) {
      if (job.kind === 'draft' && job.status === 'completed' &&
          jobList.find(item => item.id === job.id)?.status !== 'completed') state.reviews.delete(job.resource_id);
    }
    const changed = !state.tasksLoaded || state.tasksError || JSON.stringify(jobList) !== JSON.stringify(page.items);
    jobList = page.items;
    if (!taskProjectOptions().some(([id]) => String(id) === String(state.taskProjectId))) state.taskProjectId = 'all';
    state.tasksLoaded = true;
    if (state.tasksError && state.screen === 'tasks') notify();
    state.tasksError = null;
    if (completed) notify('任务完成，请去任务中心查看。', '', 3000);
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
}

function openTasks(category = state.taskCategory, projectId = state.taskProjectId) {
  stopProgress();
  state.taskCategory = category;
  state.taskProjectId = projectId;
  state.taskMenuOpen = true;
  state.screen = 'tasks';
  render();
  void loadJobs(false);
}

function taskCenterView() {
  const category = state.taskCategory;
  const projectId = state.taskProjectId;
  const jobs = sortedJobs(jobList.filter(job => taskInCategory(job, category) && taskInProject(job, projectId)));
  const filtered = jobs.filter(job => state.taskStatus === 'all' || job.status === state.taskStatus);
  const categoryFilters = element('nav', {class: 'task-category-filters', 'aria-label': '任务类型'},
    taskCategories.filter(([kind]) => kind !== 'other' || jobList.some(job => !knownTaskKinds.has(job.kind))).map(([kind, title]) => {
      const count = jobList.filter(job => taskInCategory(job, kind)).length;
      return element('button', {type: 'button', class: 'button secondary', 'data-task-category': kind,
        'aria-pressed': String(category === kind), click: () => openTasks(kind)}, `${title}（${count}）`);
    }));
  const projectFilters = element('nav', {class: 'task-project-filters', 'aria-label': '小说'},
    taskProjectOptions().map(([id, title]) => {
      const count = jobList.filter(job => taskInCategory(job, category) && taskInProject(job, id)).length;
      return element('button', {type: 'button', class: 'button secondary', 'data-task-project': id,
        'aria-pressed': String(String(projectId) === String(id)), click: () => openTasks(category, id)},
      `${title}（${count}）`);
    }));
  const statuses = [['running', '生成中'], ['all', '全部状态'], ['completed', '已完成'], ['failed', '未完成']];
  const filters = element('nav', {class: 'task-status-filters', 'aria-label': '任务状态'}, statuses.map(([status, title]) =>
    element('button', {type: 'button', class: 'button secondary', 'data-task-status': status,
      'aria-pressed': String(state.taskStatus === status), click: () => { state.taskStatus = status; render(); }},
    `${title}（${jobs.filter(job => status === 'all' || job.status === status).length}）`)));
  const emptyMessage = !state.tasksLoaded && !state.tasksError ? '正在读取任务…' :
    state.tasksError ? '暂时无法读取任务，请点击刷新任务重试。' :
    state.taskStatus === 'running' ? '此分类暂无正在生成的任务，可切换「全部状态」查看历史任务。' : '此分类暂无符合条件的任务。';
  const filterGroup = (label, content) => element('div', {class: 'task-filter-group'},
    element('strong', {class: 'task-filter-label'}, label), content);
  return panel(`任务中心 · ${taskCategoryName(category)} · ${taskProjectName(projectId)}`, stack(
    muted('汇总当前账号所有小说的后台任务，每 4 秒自动更新；点击查看进度后可查看实时内容和生成结果。'),
    filterGroup('种类', categoryFilters),
    filterGroup('小说', projectFilters),
    filterGroup('生成状态', filters),
    state.tasksError ? prose(`任务刷新失败：${state.tasksError}。下方保留上次读取的任务。`) : null,
    ...(filtered.length ? filtered.map(jobRow) : [muted(emptyMessage)])
  ), `共 ${jobs.length} 项任务，${jobs.filter(job => job.status === 'running').length} 项正在生成。`,
  button('刷新任务', () => loadJobs(false)));
}

function renderTaskResult(job, result) {
  if (job.kind === 'draft') state.reviews.delete(job.resource_id);
  const content = [];
  if (job.kind === 'review') content.push(reviewView(result));
  else if (job.kind === 'consistency') content.push(consistencyReportView(result));
  else {
    let message = '生成结果已保存，可打开小说查看。';
    if (job.kind === 'worldbuilding') message = result.persistence?.world_id ? '世界观已保存。' : '生成已完成，请核对保存状态。';
    if (job.kind === 'outline') message = `大纲 v${result.version_no} 已保存，共 ${result.volume_ids.length} 卷。`;
    if (job.kind === 'plan') message = `已保存 ${result.chapters.length} 个计划章节。`;
    if (job.kind === 'draft') message = `正文已保存，共 ${result.word_count} 字；人物正文进展可另行分析。`;
    if (job.kind === 'batch_draft') message = `已按顺序保存 ${result.completed_count} 章正文，并完成每章人物进展分析。`;
    if (job.kind === 'character_progress') message = `已分析并保存本章 ${result.updated_count} 位角色的人物正文进展。`;
    if (job.kind === 'characters') {
      const counts = [['protagonist', '主角'], ['important', '重要配角'], ['supporting', '配角']]
        .filter(([kind]) => result.counts[kind]).map(([kind, label]) => `${label} ${result.counts[kind]} 位`).join('、');
      message = !result.character_ids.length && result.profile_character_ids?.length ?
        `已保存 ${result.profile_character_ids.length} 位角色的阶段档案。` :
        `已新增 ${result.character_ids.length} 位角色${counts ? `（${counts}）` : ''}${result.reused_character_ids?.length ? `，复用 ${result.reused_character_ids.length} 位配角并保存本卷新戏份` : ''}${result.profile_character_ids?.length ? '，阶段档案已一并保存' : ''}。`;
    }
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
      if (result.volume_id) {
        state.tab = 'outline';
        await loadOutline(result.outline_id || job.resource_id, result.volume_id);
      } else {
        state.tab = 'characters';
        if (result.outline_id) state.characterOutlineId = result.outline_id;
        await loadCharacters();
      }
    } else if (job.kind === 'character_progress') {
      state.tab = 'characters';
      await loadCharacters();
    } else if (job.kind === 'outline') {
      state.tab = 'outline';
      await loadOutline(result.outline_id);
    } else if (['plan', 'draft', 'batch_draft', 'review'].includes(job.kind)) {
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

function notify(text = '', kind = '', duration = 0) {
  const node = $('notice');
  if (noticeTimer) { clearTimeout(noticeTimer); noticeTimer = null; }
  node.hidden = !text;
  node.textContent = text;
  node.dataset.kind = kind;
  node.classList.toggle('toast', Boolean(text && duration));
  if (text && duration) {
    noticeTimer = setTimeout(() => {
      node.hidden = true;
      node.classList.remove('toast');
      noticeTimer = null;
    }, duration);
  }
}

function setBusy(message = '') {
  state.busy = Boolean(message);
  $('activity').hidden = !message;
  $('activity-text').textContent = message;
  $('new-project').disabled = state.busy;
  $('refresh').disabled = state.busy;
  $('open-trash').disabled = state.busy;
  $('account-logout').disabled = state.busy;
  $('account-password-change').disabled = state.busy;
  renderLibrary();
}

function connection(message, status) {
  $('connection').textContent = message;
  $('connection').dataset.state = status;
}

function renderLibrary() {
  $('library-title').textContent = account?.is_admin ? '全部账号的小说' : '项目书架';
  $('library-owner-control').hidden = !account?.is_admin;
  const owners = new Map(state.projects.map(project => [String(project.account_id ?? 'unassigned'), projectOwner(project)]));
  $('library-owner').replaceChildren(element('option', {value: 'all'}, '全部账号'),
    ...[...owners].map(([id, name]) => element('option', {value: id}, name)));
  if (state.libraryOwner !== 'all' && !owners.has(state.libraryOwner)) state.libraryOwner = 'all';
  $('library-owner').value = state.libraryOwner;
  const projects = state.projects.filter(project => !account?.is_admin || state.libraryOwner === 'all' ||
    String(project.account_id ?? 'unassigned') === state.libraryOwner);
  const projectButton = project => element('button',
    {type: 'button', class: 'project-link', 'aria-current': String(state.project?.id === project.id),
      disabled: state.busy, click: () => openProject(project.id)},
    element('span', {class: 'project-badge', 'aria-hidden': 'true'}, Array.from(project.title || '未')[0]),
    element('span', {}, element('strong', {}, project.title || '未命名小说'),
      element('small', {}, account?.is_admin ? `${project.genre || '小说'} · ${projectOwner(project)}` : project.genre || '小说')));
  if (!projects.length) {
    $('projects').replaceChildren(element('p', {class: 'rail-note'},
      state.libraryError ? '书架读取失败，点击刷新重试。' : '还没有小说项目。'));
    return;
  }
  if (!account?.is_admin) {
    $('projects').replaceChildren(...projects.map(projectButton));
    return;
  }
  const ownProjects = projects.filter(project => String(project.account_id) === String(account.id));
  const otherProjects = projects.filter(project => String(project.account_id) !== String(account.id));
  const projectGroup = (title, rows, emptyText) => element('section', {class: 'project-group'},
    element('h3', {class: 'project-group-title'}, title),
    rows.length ? rows.map(projectButton) : element('p', {class: 'rail-note'}, emptyText));
  $('projects').replaceChildren(
    projectGroup('我的小说', ownProjects, '暂无自己的小说。'),
    projectGroup('其他账号小说', otherProjects, '暂无其他账号小说。'));
}

$('library-owner').onchange = event => { state.libraryOwner = event.target.value; renderLibrary(); };

async function loadTrash() {
  const token = key('trash');
  state.trashLoading = true;
  state.trashError = null;
  render();
  try {
    const items = await api('/projects/trash');
    if (current('trash', token)) state.trash = items;
  } catch (error) {
    if (current('trash', token)) state.trashError = error.message;
  } finally {
    if (current('trash', token)) { state.trashLoading = false; render(); }
  }
}

function openTrash() {
  stopProgress();
  state.screen = 'trash';
  state.trash = null;
  void loadTrash();
  render();
}

async function moveProjectToTrash() {
  const project = state.project;
  if (!project || readOnlyProject() || state.busy) return;
  if (!window.confirm(`确定将《${project.title || '未命名小说'}》移入回收站吗？七天后将自动彻底删除。`)) return;
  const requestingAccount = account;
  setBusy('正在移入回收站…');
  try {
    await api(`/projects/${project.id}`, {method: 'DELETE'});
    if (account !== requestingAccount) return;
    notify('小说已移入回收站，七天后会自动清理。');
    await loadLibrary();
  } catch (error) {
    if (account === requestingAccount) notify(`移入回收站失败：${error.message}`, 'error');
  } finally {
    if (account === requestingAccount) { setBusy(); render(); }
  }
}

async function restoreTrashProject(project) {
  if (state.busy) return;
  const requestingAccount = account;
  setBusy('正在恢复小说…');
  try {
    await api(`/projects/${project.id}/restore`, {method: 'POST'});
    if (account !== requestingAccount) return;
    notify(`《${project.title || '未命名小说'}》已恢复。`);
    await Promise.all([loadTrash(), loadLibrary(project.id, {preserveScreen: false})]);
  } catch (error) {
    if (account === requestingAccount) notify(`恢复失败：${error.message}`, 'error');
  } finally {
    if (account === requestingAccount) { setBusy(); render(); }
  }
}

async function purgeTrashProject(project) {
  if (state.busy) return;
  if (!window.confirm(`确定立即彻底删除《${project.title || '未命名小说'}》吗？所有世界观、大纲、角色、章节和档案都将删除且无法恢复。`)) return;
  const requestingAccount = account;
  setBusy('正在彻底删除小说及关联数据…');
  try {
    await api(`/projects/${project.id}/purge`, {method: 'DELETE'});
    if (account !== requestingAccount) return;
    notify('小说及其所有关联数据已彻底删除。');
    await loadTrash();
    await loadLibrary();
  } catch (error) {
    if (account === requestingAccount) notify(`彻底删除失败：${error.message}`, 'error');
  } finally {
    if (account === requestingAccount) { setBusy(); render(); }
  }
}

function trashView() {
  if (state.trashError) return panel('回收站', stack(prose(`读取失败：${state.trashError}`), button('重试', loadTrash, 'secondary')),
    '删除超过七天的小说会由后台自动清理。');
  if (state.trashLoading || !state.trash) return empty('正在读取回收站', '请稍候。');
  const items = state.trash;
  return panel('回收站', stack(
    muted('移入回收站的小说会保留七天，之后自动彻底删除。你也可以立即恢复或永久删除。'),
    items.length ? items.map(project => element('article', {class: 'trash-row'},
      element('div', {}, element('strong', {}, project.title || '未命名小说'),
        muted(`${project.genre || '小说'} · 将于 ${new Date(project.purge_at).toLocaleString()} 自动清理`)),
      actions(button('恢复小说', () => restoreTrashProject(project), 'secondary', state.busy),
        button('立即彻底删除', () => purgeTrashProject(project), 'danger', state.busy)))) :
      [muted('回收站为空。')]
  ), '小说删除后可在这里恢复。', button('刷新回收站', loadTrash, 'secondary', state.busy));
}

function rememberedProject() {
  try { return Number(localStorage.getItem(`mojian-project-${account.id}`)); } catch { return null; }
}

async function loadLibrary(preferred = state.project?.id ?? rememberedProject(), options = {}) {
  const token = key('library');
  const previousScreen = state.screen;
  const previousProjectId = state.project?.id;
  const previousTab = state.tab;
  const previousChapterId = state.chapterId;
  const previousCharacterOutlineId = state.characterOutlineId;
  const preserveScreen = options.preserveScreen ?? ['tasks', 'progress', 'trash'].includes(previousScreen);
  connection('连接中', 'waiting');
  try {
    const projects = await allRows('/projects');
    if (!current('library', token)) return;
    state.projects = projects.reverse();
    state.libraryError = false;
    connection('数据已连接', 'ready');
    renderLibrary();
    const target = state.projects.find((project) => project.id === preferred) ?? state.projects[0];
    if (preserveScreen) {
      if (previousProjectId && state.projects.some(project => project.id === previousProjectId)) {
        state.project = state.projects.find(project => project.id === previousProjectId);
      } else {
        state.project = null;
      }
      render();
    } else if (target) {
      await openProject(target.id, {preserveView: target.id === previousProjectId && previousScreen === 'project',
        tab: previousTab, chapterId: previousChapterId, characterOutlineId: previousCharacterOutlineId});
    }
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

async function openProject(projectId, options = {}) {
  const preserveView = options.preserveView === true;
  stopProgress();
  state.screen = 'project';
  const token = key('project');
  for (const name of ['outline', 'volume', 'chapters', 'chapter', 'characters', 'consistency']) key(name);
  Object.assign(state, {project: state.projects.find((item) => item.id === projectId) ?? null, worlds: [], outlines: [],
    tab: preserveView ? (options.tab || 'overview') : 'overview', outline: null, volumes: null, volume: null, beats: null, page: null,
    chapter: null, chapterId: preserveView ? options.chapterId ?? null : null, chapterLoading: false, chapterListError: null, outlineError: null,
    volumeError: null, projectLoading: true, projectError: null,
    characters: null, characterProfiles: null, characterProgress: null, characterError: null, characterLoading: false, characterOutlineId: preserveView ? options.characterOutlineId ?? null : null,
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
      project.tone ? pill(project.tone) : null,
      account?.is_admin ? pill(`所属账号：${projectOwner(project)}`) : null),
    readOnlyProject() ? muted('管理员只读查看：可阅读和导出，编辑与生成由所属账号操作。') : null,
    actions(button('导出小说', exportDialog, 'secondary', state.busy || state.projectLoading || Boolean(state.projectError)),
      mutationButton('移入回收站', moveProjectToTrash, 'secondary', state.busy || state.projectLoading || Boolean(state.projectError))));
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
  if (state.screen === 'trash') {
    view.replaceChildren(trashView());
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
    [['overview', '概览'], ['characters', '角色'], ['outline', '大纲与分卷'], ['chapters', '章节与正文'], ['consistency', '一致性核对']]
      .filter(([tab]) => tab !== 'consistency' || !readOnlyProject()).map(([tab, title]) =>
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
  if (tab === 'characters' && !state.characterLoading) await loadCharacters();
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
    muted('多选章节，先读取指定大纲、人物档案、人物关系及相关设定，再逐项对照正文。每个问题必须有档案与正文原文依据，资料不足时说明限制。'),
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
  const kinds = {project: '小说设定', outline: '大纲', volume: '分卷', plot_beat: '剧情节点', character: '人物档案',
    character_relationship: '人物关系', volume_character: '本卷戏份', world: '世界观', region: '地域档案',
    power_system: '力量体系', power_rank: '力量等级', cheat_system: '金手指设定', faction: '势力档案', chapter: '章节'};
  const fieldLabels = {content: '正文', summary: '概要', personality: '性格', background: '背景', goal: '目标', motivation: '动机',
    weakness: '弱点', character_arc: '成长弧线', abilities: '能力', rules: '规则', history: '历史', cultures: '文化',
    central_conflict: '核心冲突', themes: '主题', protagonist_goal: '主角目标', main_conflict: '主要冲突', climax: '高潮', ending_hook: '结尾悬念', foreshadowing: '伏笔',
    scenes: '戏份安排', relationship_type: '关系类型', description: '描述', limitations: '限制', capabilities: '能力范围',
    growth_rules: '成长规则', breakthrough_condition: '突破条件', source: '力量来源', functions: '功能', costs: '代价',
    growth_path: '成长路径', story_secret: '剧情秘密', relationships: '势力关系', territory: '领地', status: '人物状态'};
  return panel('一致性核对报告', stack(prose(report.summary),
    muted(`对照大纲 v${report.outline_version} · 读取时间：${new Date(report.checked_at).toLocaleString()}`),
    muted(`核对范围：${report.chapters.map(row => `第 ${row.chapter_number} 章 · ${row.title}`).join('、')}`),
    report.reference_materials?.length ? details(['已读取的对照档案', report.reference_materials
      .map(row => `${kinds[row.kind] || row.kind} · ${row.label}`).join('\n')]) : null,
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
    const outlineId = state.characterOutlineId || state.outlines[0]?.id;
    const [rows, profiles, progress] = await Promise.all([
      allRows('/characters', {project_id: projectId}),
      outlineId ? api(`/writing/projects/${projectId}/character-profiles`, {filters: {outline_id: outlineId}}) : Promise.resolve({items: []}),
      api(`/writing/projects/${projectId}/character-progress`),
    ]);
    if (current('characters', token)) { state.characters = rows; state.characterProfiles = profiles; state.characterProgress = progress; }
  } catch (error) {
    if (current('characters', token)) state.characterError = error.message;
  } finally {
    if (current('characters', token)) { state.characterLoading = false; render(); }
  }
}

async function moreCharacterProgress() {
  const projectId = state.project.id;
  const previous = state.characterProgress;
  if (!previous?.has_more || state.characterLoading) return;
  const request = key('characters');
  state.characterLoading = true;
  render();
  try {
    const page = await api(`/writing/projects/${projectId}/character-progress`, {filters: {offset: previous.items.length}});
    if (current('characters', request)) state.characterProgress = {...page, items: [...previous.items, ...page.items]};
  } catch (error) { if (current('characters', request)) notify(`正文进展读取失败：${error.message}`, 'error'); }
  finally { if (current('characters', request)) { state.characterLoading = false; render(); } }
}

function characterProgressView(id) {
  const entries = (state.characterProgress?.items || []).filter(row => row.character_id === id);
  return stack(rule(), element('h3', {}, '正文进展（已发生）'),
    ...entries.map(row => element('details', {class: 'character-fact'},
      element('summary', {}, `第 ${row.chapter_number} 章 · ${row.title || '未命名章节'}`),
      details(['本章行动与结果', row.actions], ['截至本章状态', row.current_state], ['人物关系', row.relationships]),
      element('details', {}, element('summary', {}, '正文依据'),
        ...row.evidence.map(quote => element('blockquote', {class: 'consistency-quote'}, quote))))),
    entries.length ? null : muted('尚无已分析的正文进展；打开章节后点击「分析人物进展」即可单独生成。'));
}

function characterView() {
  const outlineId = state.characterOutlineId || state.outlines[0]?.id;
  const version = element('select', {'aria-label': '配角生成使用的大纲版本',
    change: (event) => { state.characterOutlineId = Number(event.target.value); state.characterProfiles = null; void loadCharacters(); }},
    state.outlines.map(row => element('option', {value: row.id, selected: row.id === outlineId}, `大纲 v${row.version_no}`)));
  const controls = panel(readOnlyProject() ? '角色档案浏览' : '角色生成智能体', stack(
    muted(readOnlyProject() ? '选择大纲版本查看各卷角色档案及正文进度。' : '根据当前世界观建立主角与重要配角；大纲完成后，再补充推动剧情的其他配角。任务在后台生成，可通过「查看进度」查看实时内容。'),
    actions(mutationButton('生成主角与重要配角', coreCharacterDialog, 'primary', state.busy || state.worlds.length !== 1),
      state.outlines.length ? version : null,
      mutationButton('根据大纲补充配角', () => supportingCharacterDialog(), 'secondary', state.busy || !state.outlines.length || state.worlds.length !== 1),
      button('刷新角色', loadCharacters, 'secondary', state.characterLoading)),
    muted('正文生成和重写只保存正文；打开已保存章节后点击「分析人物进展」，单独提取已发生的行动、当前状态及关系变化，并保留正文原句依据。基础设定、大纲计划与正文进展分开展示；手动修改正文会清除该章旧进展，需重新分析。'),
    state.worlds.length !== 1 ? muted('请先设置唯一的当前世界观。') : null,
    !state.outlines.length ? muted('生成大纲后即可补充剧情配角。') : null));
  if (state.characterError) return stack(controls, empty('角色读取失败', state.characterError, button('重试', loadCharacters)));
  if (state.characters === null) return stack(controls, muted('正在读取角色…'));
  if (!state.characters.length) return stack(controls, empty('尚未建立角色', '先生成主角与重要配角，为后续大纲和正文提供人物设定。'));
  return stack(controls, panel(`角色档案 · ${state.characters.length} 位`, stack(...state.characters.map(row =>
    element('details', {class: 'character-card'}, element('summary', {}, element('strong', {}, row.name), ' ', pill(row.role)),
      details(['简介与剧情作用', row.summary], ['性格', row.personality], ['背景经历', row.background],
        ['目标', row.goal], ['动机', row.motivation], ['弱点', row.weakness], ['成长弧线', row.character_arc],
        ['能力', row.abilities], ['首次出场章节', row.first_appearance_chapter ? String(row.first_appearance_chapter) : null]),
      characterProgressView(row.id),
      ...(state.characterProfiles?.items || []).filter(profile => profile.character_id === row.id).map(profile =>
        stack(rule(), element('h3', {}, `第 ${profile.volume_number} 卷 · ${profile.volume_title}`),
          prose(profile.scenes), muted(profileProgress(profile.progress)))),
      mutationButton('生成阶段档案', () => characterProfileDialog(null, row.id), 'secondary',
        state.busy || !outlineId || state.worlds.length !== 1)))),
    state.characterProgress?.has_more ? button('加载更多正文进展', moreCharacterProgress, 'secondary', state.characterLoading) : null));
}

function profileProgress(progress) {
  if (!progress?.planned_chapters) return '当前写作进度：本卷尚未规划章节；档案内容为大纲计划。';
  return `当前写作进度：本卷 ${progress.planned_chapters} 章中有 ${progress.written_chapters} 章已保存正文` +
    `${progress.last_written_chapter ? `，最近写到第 ${progress.last_written_chapter} 章` : ''}。正文数量不代表角色阶段结果已发生。`;
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
  Object.assign(state, {volume: state.volumes.find((item) => item.id === id), beats: null, volumeCast: null, volumeError: null});
  render();
  try {
    const [beats, cast, characters] = await Promise.all([
      allRows('/plot-beats', {volume_id: id}), allRows('/volume-characters', {volume_id: id}),
      allRows('/characters', {project_id: state.project.id})]);
    if (current('volume', token)) {
      state.beats = beats;
      state.characters = characters;
      state.volumeCast = cast.map(row => ({...row, name: characters.find(person => person.id === row.character_id)?.name || '角色已移除'}));
    }
  } catch (error) {
    if (current('volume', token)) state.volumeError = error.message;
  } finally { if (current('volume', token)) render(); }
}

function outlineView() {
  const create = mutationButton('生成新大纲', outlineDialog, 'primary', state.busy || state.worlds.length !== 1);
  if (!state.outlines.length) return empty('还没有大纲', state.worlds.length === 1 ?
    '以当前世界观生成第一版大纲，分卷和剧情节点会一并保存。' : '需要先为项目设置唯一的当前世界观。', create);
  const versionSelect = element('select', {'aria-label': '大纲版本', change: (event) => loadOutline(Number(event.target.value))},
    state.outlines.map((row) => element('option', {value: row.id, selected: state.outline?.id === row.id},
      `版本 ${row.version_no} · ${statusNames[row.status] ?? row.status}`)));
  const header = panel('大纲版本', state.outline ? details(['全书摘要', state.outline.summary],
    ['核心冲突', state.outline.central_conflict], ['主题', state.outline.themes]) : muted('正在读取大纲…'), null,
  actions(versionSelect, state.outline ? mutationButton('重写大纲', () => outlineRewriteDialog(state.outline), 'secondary',
    state.busy || state.worlds.length !== 1 || !state.volumes?.length) : null, create));
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
    ['高潮', v.climax], ['结尾悬念', v.ending_hook]), rule(), element('h3', {}, '剧情节点'), beats,
    state.volumeCast?.length ? stack(rule(), element('h3', {}, '本卷角色阶段档案'),
      ...state.volumeCast.map(row => stack(element('strong', {}, row.name), prose(row.scenes)))) : null), `第 ${v.volume_number} 卷`,
  actions(mutationButton('生成本卷配角', () => supportingCharacterDialog(v), 'secondary', state.busy || state.worlds.length !== 1),
    mutationButton('生成已有角色阶段档案', () => characterProfileDialog(v), 'secondary', state.busy || state.worlds.length !== 1),
    mutationButton('规划章节 →', () => planDialog(v), 'primary', state.busy || state.worlds.length !== 1))) : empty('请选择分卷', '打开一卷查看剧情节点。');
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
    if (next) await loadChapter(next);
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
    button('下一页', () => loadChapters(page.offset + pageSize, null), 'secondary', !page.has_more || state.chapterLoading)) : null),
  null, actions(mutationButton('批量生成正文', batchDraftDialog, 'primary',
    state.busy || state.worlds.length !== 1 || readOnlyProject())));
  const chapter = state.chapter;
  if (!chapter) return element('div', {class: 'layout reading'}, list, empty(state.chapterError ? '无法读取章节' : '正在打开章节',
    state.chapterError || '请稍候。', state.chapterError ? button('重试', () => loadChapter(state.chapterId)) : null));
  const hasContent = Boolean(chapter.content.trim());
  const canEdit = ['planned', 'draft'].includes(chapter.status);
  const section = panel(chapter.title || '未命名章节', stack(details(['章节规划', chapter.summary]), rule(),
    hasContent ? element('article', {class: 'reader', tabindex: 0, 'aria-label': '章节正文'}, chapter.content) : muted('正文还没开始。可以生成初稿，也可以手动写。')),
  `第 ${chapter.chapter_number} 章 · ${statusNames[chapter.status] ?? chapter.status} · ${chapter.word_count ?? 0} 字`, actions(
    !hasContent && chapter.status === 'planned' ? mutationButton('生成正文', () => draftDialog(chapter), 'primary', state.busy || state.worlds.length !== 1) : null,
    hasContent ? mutationButton('重写正文', () => draftDialog(chapter, true), 'primary', state.busy || state.worlds.length !== 1) : null,
    canEdit ? mutationButton('编辑章节', () => editDialog(chapter), 'secondary', state.busy) : null,
    hasContent ? mutationButton('分析人物进展', () => progressAnalysisDialog(chapter), 'secondary', state.busy || state.worlds.length !== 1) : null,
    hasContent ? mutationButton('审读本章', () => reviewDialog(chapter), 'secondary', state.busy || state.worlds.length !== 1) : null));
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
  $('action-fields').replaceChildren(...options.fields.map(({label, type, value, choices, change, ...attributes}, index) => {
    const input = type === 'select' ? element('select', {...attributes, change}, choices.map(choice =>
      element('option', {value: choice.value}, choice.label))) :
      type === 'textarea' ? element('textarea', attributes) : element('input', {...attributes, type});
    input.value = value ?? '';
    if (index === 0) input.autofocus = true;
    return element('label', {class: 'control'}, label, input);
  }));
  if (options.extra) $('action-fields').append(options.extra);
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
  const form = new FormData(event.currentTarget);
  const data = Object.fromEntries(form);
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
      const payload = submitted.payload ? submitted.payload(data, form) : data;
      receipt = submitted.taskPath ? await post(submitted.taskPath, payload) : await submitted.send(payload);
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

function exportDialog() {
  const projectId = state.project.id;
  const requestingAccount = account;
  const selection = new Set();
  let chapterPage = null;
  let loading = false;
  const picker = element('fieldset', {class: 'export-chapters', hidden: true},
    element('legend', {}, '选择导出章节'));
  const count = muted('已选择 0 章（可跨页选择）');
  const chapterList = element('div', {class: 'mini-list'});
  const paging = element('div', {class: 'paging'});
  function updateCount() { count.textContent = `已选择 ${selection.size} 章（可跨页选择）`; }
  function renderChoices() {
    updateCount();
    chapterList.replaceChildren(...chapterPage.items.map(row => {
      const hasContent = Boolean(row.content?.trim());
      return element('label', {class: 'reuse-character'},
        element('input', {type: 'checkbox', value: row.id, checked: selection.has(row.id),
          disabled: !hasContent, 'aria-label': `导出第 ${row.chapter_number} 章`, change: event => {
            if (event.target.checked) selection.add(row.id);
            else selection.delete(row.id);
            updateCount();
          }}),
        element('span', {}, element('strong', {}, `第 ${row.chapter_number} 章 · ${row.title || '未命名'}`),
          muted(hasContent ? `${row.word_count ?? 0} 字` : '暂无正文，无法导出')));
    }));
    if (!chapterPage.items.length) chapterList.append(muted('暂无章节。'));
    paging.replaceChildren(
      button('上一页', () => loadChoices(chapterPage.offset - pageSize), 'secondary', !chapterPage.offset),
      `第 ${Math.floor(chapterPage.offset / pageSize) + 1} 页`,
      button('下一页', () => loadChoices(chapterPage.offset + pageSize), 'secondary', !chapterPage.has_more));
  }
  async function loadChoices(offset = 0) {
    if (loading) return;
    loading = true;
    chapterList.replaceChildren(muted('正在读取章节…'));
    paging.replaceChildren();
    try {
      const page = await api('/chapters', {filters: {project_id: projectId, limit: pageSize, offset}});
      if (task !== options || !action.open || account !== requestingAccount) return;
      chapterPage = page;
      renderChoices();
    } catch (error) {
      if (task === options && action.open && account === requestingAccount) {
        chapterList.replaceChildren(muted(`章节读取失败：${error.message}`), button('重试读取章节', () => loadChoices(offset)));
      }
    } finally { loading = false; }
  }
  picker.append(count, actions(
    button('全选本页', () => {
      if (loading || !chapterPage) return;
      for (const row of chapterPage.items) if (row.content?.trim()) selection.add(row.id);
      renderChoices();
    }),
    button('清空选择', () => { selection.clear(); updateCount(); if (!loading && chapterPage) renderChoices(); })
  ), chapterList, paging);
  const capacitor = window.Capacitor;
  const nativeExport = capacitor?.isNativePlatform?.() && capacitor.isPluginAvailable?.('NovelExport') ?
    (capacitor.Plugins?.NovelExport || capacitor.registerPlugin('NovelExport')) : null;
  const options = {title: '导出小说', description: '下载 ZIP 压缩包，每章一个文件，按全书章号排序。可导出全部正文或跨页勾选章节，自动跳过空正文；附带的大纲单独保存为文件。正在生成但尚未保存的内容不包含在压缩包中。',
    submit: nativeExport ? '导出并选择保存位置' : '导出并下载', pending: '正在准备导出文件…', fields: [
      {name: 'format', label: '章节文件格式', type: 'select', value: 'txt', choices: [
        {value: 'txt', label: 'TXT 文本（适合阅读）'}, {value: 'md', label: 'Markdown（保留标题层级）'}]},
      {name: 'scope', label: '导出范围', type: 'select', value: 'all', choices: [
        {value: 'all', label: '全部章节'}, {value: 'selected', label: '选择章节'}], change: event => {
        picker.hidden = event.target.value !== 'selected';
        if (!picker.hidden && !chapterPage) void loadChoices();
      }},
      {name: 'outline_id', label: '附带大纲', type: 'select', value: '', choices: [
        {value: '', label: '不附带大纲，只导出正文'},
        ...state.outlines.map(row => ({value: row.id, label: `大纲 v${row.version_no}`}))]},
    ], extra: picker,
    send: async ({format, outline_id, scope}) => {
      if (scope === 'selected' && !selection.size) throw new Error('请至少选择一个有正文的章节。');
      const data = scope === 'selected' ? {format, ...(outline_id ? {outline_id: Number(outline_id)} : {}),
        chapter_ids: [...selection]} : null;
      const query = new URLSearchParams({format, ...(outline_id ? {outline_id} : {})});
      const url = `${API_BASE_URL}/projects/${projectId}/export${data ? '' : `?${query}`}`;
      if (nativeExport) {
        const token = getAuthToken();
        try { return {...await nativeExport.saveNovel({url, token, format, ...(data ? {data} : {})}), native: true}; }
        catch (error) {
          if (String(error.code) === '401' && getAuthToken() === token) {
            setAuthToken();
            window.dispatchEvent(new Event('aitxt:unauthorized'));
          }
          throw error;
        }
      }
      const response = await authenticatedFetch(url, {cache: 'no-store', ...(data ? {
        method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data),
      } : {})});
      if (!response.ok) {
        const body = await response.json().catch(() => null);
        throw new Error(typeof body?.detail === 'string' ? body.detail : `导出失败（${response.status}）`);
      }
      let filename = `novel-${projectId}.zip`;
      const encoded = response.headers.get('Content-Disposition')?.match(/filename\*=UTF-8''([^;]+)/i)?.[1];
      if (encoded) {
        try { filename = decodeURIComponent(encoded); } catch { /* Use the fallback filename. */ }
      }
      return {blob: await response.blob(), filename};
    },
    success: result => result.cancelled ? '已取消导出保存。' : result.native ?
      `小说已保存：${result.filename}` : '小说文件已准备好，已发起下载。',
    after: ({blob, filename, native}) => {
      if (native) return;
      const url = URL.createObjectURL(blob);
      const link = element('a', {href: url, download: filename, hidden: true});
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 60000);
    },
  };
  openAction(options);
}

function outlineRewriteDialog(outline) {
  openAction({title: '重写大纲', description: `基于大纲 v${outline.version_no}、现有角色和世界观重写全书结构，结果保存为新版本。原大纲、已有章节和原卷戏份保留在原版本中。`,
    submit: '重写并保存新版本', fields: [numberField('volume_count', '重写后的分卷数', Math.min(5, state.volumes.length), 1, 5),
      {...noteField, label: '重写要求（可选）', placeholder: '例如：保留主线，加强第二卷冲突，调整结尾伏笔'}],
    taskPath: `/writing/outlines/${outline.id}/rewrite/tasks`});
}

function coreCharacterDialog() {
  const selectedOutlineId = state.outlines.find(row => row.id === state.characterOutlineId)?.id || state.outlines[0]?.id || '';
  const outlineChoices = [{value: '', label: state.outlines.length ? '自动使用最新大纲并绑定阶段档案' : '尚无大纲，暂生成基础档案'},
    ...state.outlines.map(row => ({value: row.id, label: `大纲 v${row.version_no} · 绑定主线与阶段档案`}))];
  openAction({title: '生成主角与重要配角', description: state.outlines.length ?
    '根据当前世界观和选定大纲创作核心人物；主角目标、冲突、能力边界和成长弧线必须服务大纲主线，并为各卷保存阶段档案。未指定时自动使用最新大纲。数量表示本次新增人数。' :
    '当前还没有大纲，只能生成基础人物档案；完成大纲后可再生成与主线绑定的阶段档案。数量表示本次新增人数。',
    submit: '后台生成并保存', fields: [numberField('protagonist_count', '新增主角数', 1, 1, 2),
      numberField('important_count', '新增重要配角数', 3, 0, 8),
      {name: 'outline_id', label: '依据大纲', type: 'select', value: selectedOutlineId, choices: outlineChoices}, noteField],
    payload: data => {
      const {outline_id, ...values} = data;
      return outline_id ? {...values, outline_id: Number(outline_id)} : values;
    },
    taskPath: `/writing/projects/${state.project.id}/characters/core/tasks`});
}

async function supportingCharacterDialog(preferredVolume) {
  const outlineId = preferredVolume?.outline_id || state.characterOutlineId || state.outlines[0]?.id;
  if (!outlineId) return;
  const projectId = state.project.id;
  const requestingAccount = account;
  const outline = state.outlines.find(row => row.id === outlineId);
  let volumes;
  try { volumes = await allRows('/outline-volumes', {outline_id: outlineId}); }
  catch (error) { if (account === requestingAccount) notify(`分卷读取失败：${error.message}`, 'error'); return; }
  if (account !== requestingAccount || state.project?.id !== projectId) return;
  if (!volumes.length) { notify('该大纲还没有分卷，请先完成分卷大纲。', 'error'); return; }
  const selectedVolume = volumes.find(row => row.id === preferredVolume?.id) || volumes[0];
  const reuse = element('fieldset', {class: 'reuse-characters', hidden: true});
  let requestSequence = 0;
  let selectionReady = false;
  const options = {title: '根据分卷大纲生成配角', description: `使用大纲 v${outline.version_no} 生成角色和阶段档案，写清本部分行动、阶段结果及进度关系。第二卷起可复用前卷配角；新增数可设为 0。`,
    submit: '后台生成并保存', fields: [
      {name: 'volume_id', label: '目标分卷', type: 'select', value: selectedVolume.id,
        choices: volumes.map(row => ({value: row.id, label: `第 ${row.volume_number} 卷 · ${row.title}`})),
        change: event => loadReuse(Number(event.target.value))},
      numberField('supporting_count', '新增配角数', 6, 0, 12), noteField], extra: reuse,
    taskPath: `/writing/outlines/${outlineId}/characters/supporting/tasks`,
    payload: (data, form) => {
      if (!selectionReady) throw new Error('请等待前卷配角读取完成。');
      const reuseIds = form.getAll('reuse_character_ids').map(Number);
      if (!data.supporting_count && !reuseIds.length) throw new Error('请新增至少一位配角或勾选要复用的配角。');
      return {...data, volume_id: Number(data.volume_id), reuse_character_ids: reuseIds};
    }};
  async function loadReuse(volumeId) {
    const sequence = ++requestSequence;
    const volume = volumes.find(row => row.id === volumeId);
    selectionReady = false;
    reuse.replaceChildren(element('legend', {}, '复用前卷配角'), muted('正在读取前卷配角…'));
    reuse.hidden = volume.volume_number === 1;
    if (reuse.hidden) { selectionReady = true; return; }
    try {
      const result = await api(`/writing/volumes/${volumeId}/reusable-characters`);
      if (sequence !== requestSequence || task !== options || account !== requestingAccount) return;
      reuse.replaceChildren(element('legend', {}, '复用前卷配角'),
        muted('勾选后会接续以往戏份，为角色安排本卷的新行动和冲突。'),
        ...result.items.map(row => element('label', {class: 'reuse-character'},
          element('input', {type: 'checkbox', name: 'reuse_character_ids', value: row.id}),
          element('span', {}, element('strong', {}, row.name), ' ', pill(row.role),
            ...row.previous_scenes.map(scene => muted(`第 ${scene.volume_number} 卷《${scene.title}》：${scene.scenes}`))))),
        ...(!result.items.length ? [muted('前面的分卷还没有可复用的配角，请先生成前卷配角，或在本卷新增配角。')] : []));
      selectionReady = true;
    } catch (error) {
      if (sequence !== requestSequence || task !== options || account !== requestingAccount) return;
      reuse.replaceChildren(element('legend', {}, '复用前卷配角'), muted(`读取失败：${error.message}`),
        button('重试', () => loadReuse(volumeId)));
    }
  }
  openAction(options);
  if (task === options) await loadReuse(selectedVolume.id);
}

async function characterProfileDialog(preferredVolume = null, characterId = null) {
  const outlineId = preferredVolume?.outline_id || state.characterOutlineId || state.outlines[0]?.id;
  if (!outlineId) return;
  const projectId = state.project.id;
  const requestingAccount = account;
  let volumes, characters;
  try { [volumes, characters] = await Promise.all([
    allRows('/outline-volumes', {outline_id: outlineId}), allRows('/characters', {project_id: projectId}),
  ]); }
  catch (error) { if (account === requestingAccount) notify(`档案资料读取失败：${error.message}`, 'error'); return; }
  if (account !== requestingAccount || state.project?.id !== projectId) return;
  if (!volumes.length || !characters.length) { notify('请先生成分卷大纲和角色基础档案。', 'error'); return; }
  const selected = volumes.find(row => row.id === preferredVolume?.id) || volumes[0];
  const choices = element('fieldset', {class: 'reuse-characters'}, element('legend', {}, '选择角色'),
    ...characters.map(row => element('label', {class: 'reuse-character'},
      element('input', {type: 'checkbox', name: 'profile_character_ids', value: row.id, checked: row.id === characterId}),
      element('span', {}, element('strong', {}, row.name), ' ', pill(row.role)))));
  const options = {title: '生成角色阶段档案', description: '根据大纲生成本部分行动、阶段结果和进度关系。基础人物档案保留；所选角色在目标分卷的阶段档案会更新。未来结局仅作为计划保存。',
    submit: '后台生成并保存', fields: [
      {name: 'volume_id', label: '目标分卷', type: 'select', value: selected.id,
        choices: volumes.map(row => ({value: row.id, label: `第 ${row.volume_number} 卷 · ${row.title}`})),
        change: event => { options.taskPath = `/writing/volumes/${Number(event.target.value)}/character-profiles/tasks`; }}, noteField], extra: choices,
    taskPath: `/writing/volumes/${selected.id}/character-profiles/tasks`,
    payload: (data, form) => {
      const ids = form.getAll('profile_character_ids').map(Number);
      if (!ids.length || ids.length > 20) throw new Error('请选择 1—20 位角色。');
      return {character_ids: ids, instruction: data.instruction || ''};
    }};
  openAction(options);
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

async function draftDialog(chapter, rewrite = false) {
  const projectId = state.project.id;
  const requestingAccount = account;
  let characters;
  try { characters = await api('/characters', {filters: {project_id: projectId, limit: 1}}); }
  catch (error) { if (account === requestingAccount) notify(`角色信息检查失败：${error.message}`, 'error'); return; }
  if (account !== requestingAccount || state.project?.id !== projectId) return;
  if (!characters.items.length) {
    await activate('characters');
    if (account !== requestingAccount || state.project?.id !== projectId) return;
    notify('尚未生成角色信息，请先设置数量并生成角色，再生成正文。');
    coreCharacterDialog();
    return;
  }
  if (rewrite) {
    openAction({title: '重写正文', description: `基于第 ${chapter.chapter_number} 章原文、角色和剧情资料重写。成功后替换当前正文并设为草稿，原审读建议清除；失败或期间章节被修改时保留原文。`,
      submit: '重写并替换正文', fields: [numberField('target_characters', '目标篇幅（字符）',
        Math.min(6000, Math.max(300, chapter.word_count || 2000)), 300, 6000),
        {...noteField, label: '重写要求（可选）', placeholder: '例如：保留情节，加强人物对话，减少重复描写'}],
      taskPath: `/writing/chapters/${chapter.id}/rewrite/tasks`});
    return;
  }
  openAction({title: '生成正文', description: `角色信息已就绪，为第 ${chapter.chapter_number} 章生成初稿。正文保存后，可单独分析本章人物进展。`,
    submit: '写作并保存', fields: [numberField('target_characters', '目标篇幅（字符）', 2000, 300, 6000), noteField],
    taskPath: `/writing/chapters/${chapter.id}/draft/tasks`,
  });
}

function progressAnalysisDialog(chapter) {
  openAction({title: '分析本章人物进展',
    description: `只读取第 ${chapter.chapter_number} 章已保存正文，提取已发生的人物行动、截至本章状态和关系变化。分析不会续写或修改正文。`,
    submit: '后台开始分析', fields: [{...noteField, label: '分析要求（可选）',
      placeholder: '例如：重点核对行动结果、位置变化和人物关系'}],
    taskPath: `/writing/chapters/${chapter.id}/character-progress/tasks`,
  });
}

async function batchDraftDialog() {
  const projectId = state.project.id;
  const requestingAccount = account;
  let chapters;
  try {
    chapters = await allRows('/chapters', {project_id: projectId, status: 'planned'});
  } catch (error) {
    if (account === requestingAccount) notify(`章节读取失败：${error.message}`, 'error');
    return;
  }
  if (account !== requestingAccount || state.project?.id !== projectId) return;
  const planned = chapters.filter(row => row.status === 'planned' && !row.content?.trim())
    .sort((left, right) => left.chapter_number - right.chapter_number);
  if (!planned.length) {
    notify('当前没有可批量生成的规划章节。请先完成章节规划，或检查已有正文。', 'error');
    return;
  }
  const selection = new Set();
  const count = muted('已选择 0 章（最多 20 章，任务会按章号顺序执行）');
  const choices = element('fieldset', {class: 'reuse-characters'},
    element('legend', {}, '选择待写章节'), count,
    ...planned.map(row => element('label', {class: 'reuse-character'},
      element('input', {type: 'checkbox', name: 'chapter_ids', value: row.id,
        change: event => {
          if (event.target.checked && selection.size >= 20) {
            event.target.checked = false;
            notify('批量生成一次最多选择 20 章。', 'error');
            return;
          }
          if (event.target.checked) selection.add(row.id); else selection.delete(row.id);
          count.textContent = `已选择 ${selection.size} 章（最多 20 章，任务会按章号顺序执行）`;
        }}),
      element('span', {}, element('strong', {}, `第 ${row.chapter_number} 章 · ${row.title || '未命名'}`),
        muted(row.summary || '暂无章节摘要')))));
  openAction({title: '批量生成正文',
    description: `从已有章节规划中选择多章，系统会按章号依次写作；每章正文保存并完成独立人物进展分析后才会进入下一章。`,
    submit: '后台开始批量写作', fields: [numberField('target_characters', '每章目标篇幅（字符）', 2000, 300, 6000), noteField],
    extra: choices,
    taskPath: `/writing/projects/${projectId}/chapters/batch-draft/tasks`,
    payload: (data, form) => {
      const ids = form.getAll('chapter_ids').map(Number);
      if (!ids.length) throw new Error('请至少选择一章。');
      if (ids.length > 20) throw new Error('一次最多选择 20 章。');
      return {...data, chapter_ids: ids};
    }});
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
$('open-trash').onclick = openTrash;
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
  $('account-name').textContent = `${user.display_name || user.username}${user.is_admin ? ' · 管理员' : ''}`;
  $('account-password').value = '';
  accountDialog.close();
  $('app-shell').hidden = false;
  $('new-project').disabled = false;
  $('refresh').disabled = false;
  $('open-trash').disabled = false;
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
