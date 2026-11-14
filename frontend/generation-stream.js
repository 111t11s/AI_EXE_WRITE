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

// The same incremental view is used by the worldbuilding and writing dialogs.
export function createGenerationView(root) {
  const output = root.querySelector('.generation-output');
  const thinking = root.querySelector('.generation-thinking');
  const thinkingOutput = thinking.querySelector('pre');
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

  function flush() {
    if (frame !== null) cancelAnimationFrame(frame);
    frame = null;
    thinking.hidden = !thinkingText;
    render(thinkingOutput, thinkingText);
    render(output, formatGenerationText(text) || (thinkingText ? '模型正在思考，正文将实时显示在这里…' : '等待模型返回内容…'));
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
      output.textContent = thinkingOutput.textContent = status.textContent = '';
      thinking.hidden = true;
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
      thinking.open = true;
      output.scrollTop = thinkingOutput.scrollTop = 0;
      flush();
      revealOutput = true;
      frame = requestAnimationFrame(flush);
    },
    event(event, data) {
      if (event === 'thinking' || event === 'delta') {
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
      status.textContent = message;
      root.dataset.state = 'done';
      root.setAttribute('aria-busy', 'false');
    },
    fail(message) {
      flush();
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
  relationships: '人物关系', role: '角色定位', personality: '性格', background: '背景', motivation: '动机',
  weakness: '弱点', character_arc: '人物成长', abilities: '能力', faction_name: '所属势力',
  central_conflict: '核心冲突', themes: '主题', main_conflict: '主要冲突', climax: '高潮', ending_hook: '结尾悬念',
  foreshadowing: '伏笔', content: '正文', evidence: '文中依据', suggestion: '修改建议', verdict: '审读结论'};

export function formatGenerationText(text) {
  const entries = [];
  const fields = /"((?:\\.|[^"\\])*)"\s*:\s*"((?:\\[\s\S]|[^"\\])*)/g;
  for (const match of text.matchAll(fields)) {
    const label = fieldNames[match[1]] || match[1];
    let raw = match[2];
    let value;
    // An incomplete unicode escape may be at the end of the current packet.
    for (let removed = 0; removed < 6; removed++) {
      try { value = JSON.parse(`"${raw}"`); break; }
      catch { raw = raw.slice(0, -1); }
    }
    if (value) entries.push(`${label}\n${({ready: '可用', revise: '建议修改'})[value] ?? value}`);
  }
  return entries.join('\n\n') || text;
}
