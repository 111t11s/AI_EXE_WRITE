import {authenticatedFetch} from './api-client.js';

// Decode SSE incrementally, including UTF-8 characters split across packets.
export async function readGenerationStream(response, onEvent = () => {}) {
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    const error = new Error(typeof body?.detail === 'string' ? body.detail : `请求失败（${response.status}）`);
    error.status = response.status;
    throw error;
  }
  if (!response.body || !response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('未收到实时生成连接，请刷新页面后重试。');
  }
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let eventName = 'message';
  let dataLines = [];
  let finished = false;
  let result;

  function line(value) {
    if (!value) {
      if (dataLines.length) {
        const data = JSON.parse(dataLines.join('\n'));
        if (eventName === 'error') {
          const error = new Error(data.message || '生成失败，请稍后重试。');
          error.status = data.status;
          throw error;
        }
        onEvent(eventName, data);
        if (eventName === 'result') { result = data; finished = true; }
      }
      eventName = 'message';
      dataLines = [];
    } else if (value.startsWith('event:')) eventName = value.slice(6).trim();
    else if (value.startsWith('data:')) dataLines.push(value.slice(5).replace(/^ /, ''));
  }

  function drain(eof = false) {
    // Hold a trailing CR until its possible LF arrives in the next packet.
    while (buffer.length && !finished) {
      const end = buffer.search(/[\r\n]/);
      if (end < 0 || (!eof && end === buffer.length - 1 && buffer[end] === '\r')) break;
      const size = buffer[end] === '\r' && buffer[end + 1] === '\n' ? 2 : 1;
      const value = buffer.slice(0, end);
      buffer = buffer.slice(end + size);
      line(value);
    }
    if (eof && !finished) {
      if (buffer) { line(buffer); buffer = ''; }
      line('');
    }
  }

  try {
    while (!finished) {
      const {value, done} = await reader.read();
      buffer += done ? decoder.decode() : decoder.decode(value, {stream: true});
      drain(done);
      if (done) break;
    }
    if (!finished) {
      const error = new Error('连接已中断，已接收内容保留；后台可能仍在生成或保存，请核对项目结果后再重试。');
      error.uncertain = true;
      throw error;
    }
    return result;
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}

export async function postGeneration(url, data, onEvent) {
  try {
    const response = await authenticatedFetch(url, {method: 'POST', cache: 'no-store',
      headers: {'Content-Type': 'application/json', Accept: 'text/event-stream'}, body: JSON.stringify(data)});
    return await readGenerationStream(response, onEvent);
  } catch (error) {
    if (error instanceof TypeError) {
      const interrupted = new Error('连接已中断，已接收内容保留；后台可能仍在生成或保存，请核对项目结果后再重试。');
      interrupted.uncertain = true;
      throw interrupted;
    }
    throw error;
  }
}

export async function watchGeneration(url, onEvent, {signal} = {}) {
  const response = await authenticatedFetch(url, {
    method: 'GET', cache: 'no-store', headers: {Accept: 'text/event-stream'}, signal,
  });
  return readGenerationStream(response, onEvent);
}

// The same incremental view is used by the worldbuilding and writing dialogs.
export function createGenerationView(root) {
  const output = root.querySelector('.generation-output');
  const thinking = root.querySelector('.generation-thinking');
  const thinkingOutput = thinking?.querySelector('pre');
  const status = root.querySelector('[role="status"]');
  let text = '';
  let thinkingText = '';
  let characters = 0;
  let thinkingCharacters = 0;
  let frame = null;
  let revealOutput = false;

  function render(node, value) {
    const pinned = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
    if (node.textContent !== value) node.textContent = value;
    if (pinned) node.scrollTop = node.scrollHeight;
  }

  function structured() {
    const head = text.trimStart();
    return head.startsWith('{') || head.startsWith('[');
  }

  function flush() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    if (thinking && thinkingOutput) {
      thinking.hidden = !thinkingText;
      render(thinkingOutput, formatThinkingText(thinkingText));
    }
    const formatted = structured() ? '' : formatGenerationText(text);
    render(output, formatted || (structured() ? '正在生成结构化内容，完成后展示结果…' : '等待模型返回内容…'));
    if (revealOutput) {
      (text ? output : root).scrollIntoView({block: 'nearest'});
      revealOutput = false;
    }
  }

  return {
    hide() {
      if (frame !== null) cancelAnimationFrame(frame);
      frame = null;
      text = thinkingText = '';
      characters = thinkingCharacters = 0;
      revealOutput = false;
      output.textContent = status.textContent = '';
      if (thinkingOutput) thinkingOutput.textContent = '';
      if (thinking) thinking.hidden = true;
      root.hidden = true;
      root.setAttribute('aria-busy', 'false');
    },
    begin() {
      text = '';
      thinkingText = '';
      characters = 0;
      thinkingCharacters = 0;
      root.hidden = false;
      root.dataset.state = 'running';
      root.setAttribute('aria-busy', 'true');
      status.textContent = '正在连接模型…';
      if (thinking) thinking.open = true;
      output.scrollTop = 0;
      if (thinkingOutput) thinkingOutput.scrollTop = 0;
      flush();
      revealOutput = true;
      frame = requestAnimationFrame(flush);
    },
    event(event, data) {
      if (event === 'snapshot') {
        text = data.text || '';
        thinkingText = data.thinking || '';
        characters = Array.from(text).length;
        thinkingCharacters = Array.from(thinkingText).length;
        status.textContent = data.message || '正在生成…';
        if (data.truncated) status.textContent += ' · 仅显示最近生成的内容';
        flush();
      } else if (event === 'thinking' || event === 'delta') {
        if (typeof data.text !== 'string' || !data.text) return;
        if (event === 'thinking') {
          if (!thinkingText) revealOutput = true;
          thinkingText += data.text;
          thinkingCharacters += Array.from(data.text).length;
          status.textContent = `思考中 · 已接收 ${thinkingCharacters} 字符`;
        } else {
          if (!text) revealOutput = true;
          text += data.text;
          characters += Array.from(data.text).length;
          status.textContent = `正在生成 · 已接收 ${characters} 字符`;
        }
        if (frame === null) frame = requestAnimationFrame(flush);
      } else if (event === 'status') status.textContent = data.message;
    },
    finish(message = '生成完成') {
      flush();
      if (structured()) output.textContent = '结构化内容已生成，结果见下方。';
      status.textContent = message;
      root.dataset.state = 'done';
      root.setAttribute('aria-busy', 'false');
    },
    isEmpty() {
      return !thinkingText && (!text || structured());
    },
    fail(message) {
      flush();
      if (!text || structured()) output.textContent = '任务未完成，请查看上方提示。';
      status.textContent = message;
      root.dataset.state = 'error';
      root.setAttribute('aria-busy', 'false');
    },
  };
}

const fieldNames = {title: '标题', name: '名称', genre: '小说类型', setting: '时代与背景', tone: '故事基调',
  target_length: '篇幅规划', premise: '故事构想', protagonist_goal: '主角目标', summary: '概要',
  era: '时代', history: '历史沿革', rules: '核心规则', cultures: '文化与社会', description: '描述',
  climate: '气候', resources: '资源', locations: '地点', source: '力量来源', limitations: '限制',
  growth_rules: '成长规则', breakthrough_condition: '突破条件', capabilities: '能力', cheat_type: '金手指类型',
  origin: '来历', functions: '功能', costs: '代价', growth_path: '成长路线', story_secret: '故事秘密',
  faction_type: '势力类型', goal: '目标', leader: '领袖', territory: '领地', strengths: '优势', weaknesses: '弱点',
  relationships: '人物关系', category: '分类', severity: '问题等级', role: '角色定位', personality: '性格', background: '背景', motivation: '动机',
  weakness: '弱点', character_arc: '人物成长', abilities: '能力', faction_name: '所属势力',
  central_conflict: '核心冲突', themes: '主题', main_conflict: '主要冲突', climax: '高潮', ending_hook: '结尾悬念',
  foreshadowing: '伏笔', content: '正文', evidence: '文中依据', suggestion: '修改建议', verdict: '审读结论',
  quote: '原文引文', explanation: '偏移说明', kind: '对照类型', field: '对照字段',
  actions: '本章行动与结果', current_state: '截至本章状态', discussion_hook: '话题点',
  chapter: '章节', chapters: '章节', chapter_id: '章节编号', chapter_number: '章节序号', chapter_count: '章节数',
  start_chapter: '起始章号', volume: '分卷', volumes: '分卷', volume_id: '分卷编号', volume_ids: '分卷编号',
  outline: '大纲', outline_id: '大纲编号', project: '小说', project_id: '小说编号', job_id: '任务编号',
  record_id: '档案编号', basis: '对照依据', issues: '问题', references: '引用档案', reference_materials: '对照档案',
  beats: '剧情节点', cast: '本卷角色', characters: '角色', character_ids: '角色编号', counts: '数量',
  persistence: '保存结果', warning: '提示', version_no: '大纲版本', completed_count: '完成章节数',
  updated_count: '更新数量', word_count: '字数', audience: '读者', risks: '风险', requirement_summary: '需求概括'};

const valueNames = {ready: '可用', revise: '建议修改', protagonist: '主角', important: '重要配角', supporting: '配角',
  consistent: '未发现明确偏移', drifted: '发现偏移', insufficient: '资料不足',
  plot: '剧情', character: '人物', world: '世界观', continuity: '章节衔接',
  minor: '细节建议', major: '主要问题',
  chapter: '章节', plot_beat: '剧情节点', volume: '分卷', outline: '大纲', project: '小说设定',
  character_relationship: '人物关系', volume_character: '本卷戏份', region: '地域档案',
  power_system: '力量体系', power_rank: '力量等级', cheat_system: '金手指设定', faction: '势力档案',
  content: '正文', summary: '概要', abilities: '能力', limitations: '限制', capabilities: '能力范围',
  description: '描述', scenes: '戏份安排', relationship_type: '关系类型', functions: '功能', costs: '代价',
  growth_rules: '成长规则', breakthrough_condition: '突破条件', source: '力量来源', growth_path: '成长路径',
  story_secret: '剧情秘密', territory: '领地', status: '人物状态',
  planned: '已规划', draft: '草稿', running: '进行中', completed: '已完成', failed: '未完成'};

function formatThinkingText(value) {
  if (!value) return '';
  let text = String(value);
  // 把模型思考里出现的结构化字段名和枚举值换成中文，保留推理过程但不直接展示参数名。
  text = text.replace(/"([A-Za-z_][A-Za-z0-9_]*)"\s*:/g,
    (match, key) => `${fieldNames[key] || '内容'}：`);
  text = text.replace(/"([a-z][a-z0-9_]*)"/g, (match, key) => valueNames[key] || match);
  text = text.replace(/\b(chapter|plot_beat|volume_character|character_relationship|power_system|power_rank|cheat_system|chapter_id|outline_id|record_id|basis|verdict|reference_topics)\b/gi,
    term => valueNames[term.toLowerCase()] || fieldNames[term.toLowerCase()] || term);
  text = text.replace(/[{}[\]]/g, ' ');
  if (text.length > 60000) text = `……（思考过长，仅显示最近部分）\n${text.slice(-60000)}`;
  return text.replace(/\n{3,}/g, '\n\n').trim();
}

export function formatGenerationText(text) {
  // 结构化内容只显示中文进度提示，不把字段名和枚举值直接铺到页面上。
  if (/^\s*[\[{]/.test(text)) return '';
  const entries = [];
  const fields = /"((?:\\.|[^"\\])*)"\s*:\s*"((?:\\[\s\S]|[^"\\])*)/g;
  for (const match of text.matchAll(fields)) {
    const label = fieldNames[match[1]] || '相关内容';
    let raw = match[2];
    let value;
    // An incomplete unicode escape may be at the end of the current packet.
    for (let removed = 0; removed < 6; removed++) {
      try { value = JSON.parse(`"${raw}"`); break; }
      catch { raw = raw.slice(0, -1); }
    }
    if (value) entries.push(`${label}\n${valueNames[value] ?? value}`);
  }
  return entries.join('\n\n') || text;
}
