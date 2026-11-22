import {resolveApiBaseUrl} from './api-client.js';

const base = resolveApiBaseUrl(window.AITXT_CONFIG?.apiBaseUrl);
const view = document.getElementById('public-view');
const status = document.getElementById('public-status');
const refresh = document.getElementById('public-refresh');
const workspace = document.querySelector('.public-workspace');
const names = {planning: '规划中', writing: '连载中', completed: '已完结', paused: '暂停更新'};
const pageSize = 20;
let revision = 0;
let snapshot = '';
let readerState = null;
history.scrollRestoration = 'manual';

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
        muted(`${book.author} · ${book.genre} · ${names[book.status] || '未知状态'}`),
        node('p', {class: 'book-synopsis'}, book.premise || '作者尚未填写简介。'),
        muted(`${book.chapter_count} 章 · 更新于 ${date(book.updated_at)}`),
        link('阅读书籍', {book: book.id}, 'button primary'))) :
      node('div', {class: 'panel empty'}, node('h2', {}, '公共书架还没有书籍'), muted('作者发布小说后，就会显示在这里。'))),
    node('nav', {class: 'public-pagination', 'aria-label': '公共书架分页'},
      offset > 0 ? link('上一页', {offset: Math.max(0, offset - pageSize)}) : null,
      muted(`第 ${Math.floor(offset / pageSize) + 1} 页`),
      page.has_more ? link('下一页', {offset: offset + pageSize}) : null));
}

function setDirectory(open, {restoreFocus = false} = {}) {
  if (!readerState) return;
  const hiddenFocus = !open && readerState.reading.classList.contains('is-directory-open') &&
    readerState.reading.querySelector('.public-directory-panel').contains(document.activeElement);
  readerState.reading.classList.toggle('is-directory-open', open);
  readerState.toggle.setAttribute('aria-expanded', String(open));
  readerState.panel.inert = open;
  if (open) {
    const active = readerState.directory.querySelector('[aria-current=page]');
    active?.scrollIntoView({block: 'nearest'});
    readerState.close.focus({preventScroll: true});
  } else if (restoreFocus || hiddenFocus) readerState.toggle.focus({preventScroll: true});
}

function createReader(bookId) {
  const title = node('h1');
  const metadata = muted('');
  const count = node('span', {class: 'muted'});
  const synopsis = node('div');
  const updated = muted('');
  const directory = node('nav', {class: 'public-directory', 'aria-label': '章节目录'});
  const toggle = node('button', {type: 'button', class: 'button secondary public-directory-toggle',
    'aria-controls': 'public-directory-panel', 'aria-expanded': 'false', click: () => setDirectory(true)}, '目录');
  const close = node('button', {type: 'button', class: 'button secondary public-directory-close',
    click: () => setDirectory(false, {restoreFocus: true})}, '收起目录');
  const heading = node('h2');
  const chapterMeta = muted('');
  const body = node('div', {class: 'public-reader', tabindex: '0', 'aria-label': '章节正文'});
  const footer = node('nav', {class: 'public-reader-footer', 'aria-label': '章节翻页'});
  const reading = node('div', {class: 'public-reading'},
    node('aside', {id: 'public-directory-panel', class: 'panel public-directory-panel', 'aria-label': '书籍与章节'},
      node('header', {class: 'public-directory-heading'}, node('h2', {}, '章节目录', count), close),
      node('details', {class: 'public-book-details'}, node('summary', {}, '书籍简介与更新'), synopsis, updated), directory),
    node('button', {type: 'button', class: 'public-directory-backdrop', tabindex: '-1',
      'aria-label': '关闭章节目录', click: () => setDirectory(false, {restoreFocus: true})}),
    node('article', {class: 'panel public-reader-panel'},
      node('header', {class: 'public-reader-heading'}, toggle,
        node('div', {'aria-live': 'polite', 'aria-atomic': 'true'}, heading, chapterMeta)), body, footer));
  const panel = reading.querySelector('.public-reader-panel');
  const root = node('div', {class: 'public-book-view'},
    node('header', {class: 'public-book-heading'}, link('← 返回公共书架', {}, ''), node('div', {}, title, metadata)), reading);
  return {bookId, root, reading, panel, title, metadata, count, synopsis, updated, directory, toggle, close,
    heading, chapterMeta, body, footer, chapterId: null, directoryKey: '', footerKey: ''};
}

function updateReader(book, items, chapter, {background, existing}) {
  const ui = readerState;
  const changedChapter = ui.chapterId !== (chapter?.id ?? null);
  const readerScroll = ui.body.scrollTop;
  const index = items.findIndex(item => item.id === chapter?.id);
  document.title = `${book.title} · 墨间`;
  ui.title.textContent = book.title;
  ui.title.title = book.title;
  ui.metadata.textContent = `${book.author} · ${book.genre} · ${names[book.status] || book.status}`;
  ui.count.textContent = `${items.length} 章`;
  ui.synopsis.textContent = book.premise || '作者尚未填写简介。';
  ui.updated.textContent = `更新于 ${date(book.updated_at)} · 每 30 秒同步`;
  const directoryKey = JSON.stringify(items.map(item => [item.id, chapterName(item)]));
  if (directoryKey !== ui.directoryKey) {
    const scroll = ui.directory.scrollTop;
    const focusedHref = ui.directory.contains(document.activeElement) ? document.activeElement.getAttribute('href') : null;
    ui.directory.replaceChildren(...(items.length ? items.map(item =>
      link(chapterName(item), {book: book.id, chapter: item.id}, '')) : [muted('作者尚未添加正文。')]));
    ui.directory.scrollTop = scroll;
    if (focusedHref) [...ui.directory.querySelectorAll('a')].find(entry => entry.getAttribute('href') === focusedHref)?.focus({preventScroll: true});
    ui.directoryKey = directoryKey;
  }
  for (const entry of ui.directory.querySelectorAll('a')) {
    if (Number(new URL(entry.href).searchParams.get('chapter')) === chapter?.id) entry.setAttribute('aria-current', 'page');
    else entry.removeAttribute('aria-current');
  }
  ui.heading.textContent = chapter ? chapterName(chapter) : '敬请期待';
  ui.chapterMeta.textContent = chapter ? `${chapter.word_count || 0} 字 · 更新于 ${date(chapter.updated_at)}` : '正文更新后会自动显示';
  const content = chapter?.content ?? '书籍已发布，正文更新后会自动显示在这里。';
  if (ui.body.textContent !== content) ui.body.textContent = content;
  ui.body.scrollTop = changedChapter ? 0 : readerScroll;
  const footerKey = JSON.stringify([index, items.map(item => item.id)]);
  if (footerKey !== ui.footerKey) {
    ui.footer.replaceChildren(
      index > 0 ? link('← 上一章', {book: book.id, chapter: items[index - 1].id}) :
        node('span', {class: 'button secondary', 'aria-disabled': 'true'}, '← 上一章'),
      muted(index >= 0 ? `${index + 1} / ${items.length} 章` : '等待更新'),
      index >= 0 && index + 1 < items.length ? link('下一章 →', {book: book.id, chapter: items[index + 1].id}) :
        node('span', {class: 'button secondary', 'aria-disabled': 'true'}, '下一章 →'));
    ui.footerKey = footerKey;
  }
  ui.chapterId = chapter?.id ?? null;
  if (!background) {
    setDirectory(false);
    if (existing && changedChapter) ui.body.focus({preventScroll: true});
  }
}

async function load({background = false} = {}) {
  const token = ++revision;
  const params = new URLSearchParams(location.search);
  const bookId = Number(params.get('book'));
  const offset = Math.max(0, Math.min(2 ** 32 - 1, Number(params.get('offset')) || 0));
  if (!background) {
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
      const windowScroll = window.scrollY;
      const existing = bookId && readerState?.bookId === data.book.id && view.contains(readerState.root);
      workspace.classList.toggle('is-reading', Boolean(bookId));
      if (bookId) {
        if (!existing) {
          readerState = createReader(data.book.id);
          view.replaceChildren(readerState.root);
        }
        updateReader(data.book, data.items, data.chapter, {background, existing});
      } else {
        readerState = null;
        view.replaceChildren(shelf(data, offset));
      }
      if (!existing) window.scrollTo(0, background ? windowScroll : 0);
      snapshot = next;
    }
    if (!background && bookId) setDirectory(false);
    status.hidden = true;
  } catch (error) {
    if (token !== revision) return;
    status.textContent = error.message;
    status.dataset.kind = 'error';
    status.hidden = false;
    if (!background || error.status === 404) {
      snapshot = '';
      readerState = null;
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
document.addEventListener('keydown', event => {
  if (!readerState?.reading.classList.contains('is-directory-open')) return;
  if (event.key === 'Escape') {
    setDirectory(false, {restoreFocus: true});
  } else if (event.key === 'Tab') {
    const panel = readerState.reading.querySelector('.public-directory-panel');
    const controls = [...panel.querySelectorAll('button, summary, a[href]')].filter(element => element.getClientRects().length);
    const first = controls[0];
    const last = controls.at(-1);
    if (!panel.contains(document.activeElement) || (event.shiftKey && document.activeElement === first)) {
      event.preventDefault();
      (event.shiftKey ? last : first)?.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first?.focus();
    }
  }
});
window.matchMedia('(min-width: 761px)').addEventListener('change', () => setDirectory(false));
document.addEventListener('visibilitychange', () => {
  if (!document.hidden) void load({background: true});
});
setInterval(() => {
  if (!document.hidden && view.getAttribute('aria-busy') !== 'true') void load({background: true});
}, 30000);
void load();
