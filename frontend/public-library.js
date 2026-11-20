import {resolveApiBaseUrl} from './api-client.js';

const base = resolveApiBaseUrl(window.AITXT_CONFIG?.apiBaseUrl);
const view = document.getElementById('public-view');
const status = document.getElementById('public-status');
const refresh = document.getElementById('public-refresh');
const names = {planning: '规划中', writing: '连载中', completed: '已完结', paused: '暂停更新'};
const pageSize = 20;
let revision = 0;
let snapshot = '';

function node(tag, props = {}, ...children) {
  const result = document.createElement(tag);
  for (const [name, value] of Object.entries(props)) {
    if (value == null || value === false) continue;
    if (name === 'click') result.addEventListener('click', value);
    else result.setAttribute(name, value === true ? '' : String(value));
  }
  result.append(...children.flat().filter(item => item != null));
  return result;
}

function link(text, values = {}, style = 'button secondary') {
  const query = new URLSearchParams(values).toString();
  const href = `/public.html${query ? `?${query}` : ''}`;
  return node('a', {href, class: style, click: event => {
    if (event.button !== 0 || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    history.pushState(null, '', href);
    void load();
  }}, text);
}

async function get(path, params = {}) {
  const query = new URLSearchParams(params).toString();
  const response = await fetch(`${base}/public/projects${path}${query ? `?${query}` : ''}`, {cache: 'no-store'});
  const data = await response.json().catch(() => null);
  if (!response.ok) {
    const error = new Error(typeof data?.detail === 'string' ? data.detail : '公共书架暂时无法读取，请稍后刷新。');
    error.status = response.status;
    throw error;
  }
  return data;
}

async function chapters(bookId) {
  const items = [];
  for (let offset = 0; ; offset += 100) {
    const page = await get(`/${bookId}/chapters`, {limit: 100, offset});
    items.push(...page.items);
    if (!page.has_more) return items;
  }
}

const muted = text => node('p', {class: 'muted'}, text);
const date = value => new Date(value).toLocaleString();
const chapterName = chapter => `第 ${chapter.chapter_number} 章${chapter.title ? ` · ${chapter.title}` : ''}`;

function shelf(page, offset) {
  document.title = '墨间 · 公共书架';
  return node('div', {},
    node('section', {class: 'hero'}, node('p', {class: 'eyebrow'}, '墨间 · 共享阅读'),
      node('h1', {}, '公共书架'), node('p', {}, '作者发布的故事都在这里，无需登录即可阅读。')),
    node('section', {class: 'public-grid', 'aria-label': '已发布书籍'}, page.items.length ? page.items.map(book =>
      node('article', {class: 'panel public-book'}, node('h2', {}, book.title),
        muted(`${book.author} · ${book.genre} · ${names[book.status] || book.status}`),
        node('p', {class: 'book-synopsis'}, book.premise || '作者尚未填写简介。'),
        muted(`${book.chapter_count} 章 · 更新于 ${date(book.updated_at)}`),
        link('阅读书籍', {book: book.id}, 'button primary'))) :
      node('div', {class: 'panel empty'}, node('h2', {}, '公共书架还没有书籍'), muted('作者发布小说后，就会显示在这里。'))),
    node('nav', {class: 'public-pagination', 'aria-label': '公共书架分页'},
      offset > 0 ? link('上一页', {offset: Math.max(0, offset - pageSize)}) : null,
      muted(`第 ${Math.floor(offset / pageSize) + 1} 页`),
      page.has_more ? link('下一页', {offset: offset + pageSize}) : null));
}

function reader(book, items, chapter) {
  document.title = `${book.title} · 墨间`;
  const index = items.findIndex(item => item.id === chapter?.id);
  return node('div', {}, link('← 返回公共书架'),
    node('section', {class: 'hero'}, node('p', {class: 'eyebrow'}, '公开阅读'), node('h1', {}, book.title),
      muted(`${book.author} · ${book.genre} · ${names[book.status] || book.status} · ${book.chapter_count} 章`),
      node('p', {class: 'prose'}, book.premise || '作者尚未填写简介。'),
      muted(`更新于 ${date(book.updated_at)} · 页面每 30 秒检查作者更新`)),
    node('div', {class: 'public-reading'},
      node('section', {class: 'panel'}, node('header', {class: 'panel-head'}, node('h2', {}, '章节目录')),
        node('nav', {class: 'public-directory', 'aria-label': '章节目录'}, items.length ? items.map(item => {
          const entry = link(chapterName(item), {book: book.id, chapter: item.id}, '');
          if (item.id === chapter?.id) entry.setAttribute('aria-current', 'page');
          return entry;
        }) : muted('作者尚未添加正文。'))),
      node('article', {class: 'panel'}, node('header', {class: 'panel-head'},
        node('h2', {}, chapter ? chapterName(chapter) : '敬请期待')),
        chapter ? node('div', {class: 'public-reader', tabindex: '0', 'aria-label': '章节正文'}, chapter.content) :
          muted('书籍已发布，正文更新后会自动显示在这里。'),
        node('div', {class: 'actions'},
          index > 0 ? link('← 上一章', {book: book.id, chapter: items[index - 1].id}) : null,
          index >= 0 && index + 1 < items.length ? link('下一章 →', {book: book.id, chapter: items[index + 1].id}) : null))));
}

async function load({background = false} = {}) {
  const token = ++revision;
  const params = new URLSearchParams(location.search);
  const bookId = Number(params.get('book'));
  const offset = Math.max(0, Math.min(2 ** 32 - 1, Number(params.get('offset')) || 0));
  if (!background) {
    snapshot = '';
    view.setAttribute('aria-busy', 'true');
    status.textContent = '正在读取最新内容…';
    status.hidden = false;
    status.removeAttribute('data-kind');
    refresh.disabled = true;
  }
  try {
    let data;
    if (bookId) {
      const [book, items] = await Promise.all([get(`/${bookId}`), chapters(bookId)]);
      if (token !== revision) return;
      const selected = items.find(item => item.id === Number(params.get('chapter'))) || items[0];
      const chapter = selected ? await get(`/${bookId}/chapters/${selected.id}`) : null;
      data = {book, items, chapter};
    } else {
      data = await get('', {limit: pageSize, offset});
    }
    if (token !== revision) return;
    const next = JSON.stringify(data);
    if (next !== snapshot) {
      const readerScroll = view.querySelector('.public-reader')?.scrollTop || 0;
      const directoryScroll = view.querySelector('.public-directory')?.scrollTop || 0;
      const windowScroll = window.scrollY;
      view.replaceChildren(bookId ? reader(data.book, data.items, data.chapter) : shelf(data, offset));
      if (background) {
        const body = view.querySelector('.public-reader');
        if (body) body.scrollTop = readerScroll;
        const directory = view.querySelector('.public-directory');
        if (directory) directory.scrollTop = directoryScroll;
        window.scrollTo(0, windowScroll);
      } else window.scrollTo(0, 0);
      snapshot = next;
    }
    status.hidden = true;
  } catch (error) {
    if (token !== revision) return;
    status.textContent = error.message;
    status.dataset.kind = 'error';
    status.hidden = false;
    if (!background || error.status === 404) {
      snapshot = '';
      view.replaceChildren(node('section', {class: 'panel empty'},
        node('h1', {}, error.status === 404 ? '书籍或章节已不可用' : '读取失败'),
        muted(error.status === 404 ? '作者可能已取消发布、删除书籍或更新章节。' : error.message),
        link('返回公共书架')));
    }
  } finally {
    if (token === revision) {
      view.setAttribute('aria-busy', 'false');
      refresh.disabled = false;
    }
  }
}

refresh.addEventListener('click', () => void load({background: true}));
window.addEventListener('popstate', () => void load());
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) void load({background: true});
});
setInterval(() => {
  if (!document.hidden && view.getAttribute('aria-busy') !== 'true') void load({background: true});
}, 30000);
void load();
