import assert from 'node:assert/strict';
import {test} from 'node:test';
import {createGenerationView, formatGenerationText, readGenerationStream} from '../frontend/generation-stream.js';

const encode = (text) => new TextEncoder().encode(text);
const response = (stream) => new Response(stream, {headers: {'Content-Type': 'text/event-stream'}});

test('SSE decoding preserves Chinese UTF-8 across byte and CRLF boundaries', async () => {
  const bytes = encode(': 保持连接\r\n\r\nevent: delta\r\ndata: {"text":"云泽大陆\\n灵气"}\r\n\r\n' +
    'event: result\r\ndata: {"id":2}\r\n\r\n');
  let index = 0;
  const events = [];
  const result = await readGenerationStream(response(new ReadableStream({pull(controller) {
    if (index < bytes.length) controller.enqueue(bytes.slice(index, ++index));
    else controller.close();
  }})), (name, data) => events.push([name, data]));
  assert.deepEqual(events, [['delta', {text: '云泽大陆\n灵气'}], ['result', {id: 2}]]);
  assert.deepEqual(result, {id: 2});
});

test('text arrives before the server completes, and a result is required', async () => {
  let controller;
  let gotDelta;
  const first = new Promise((resolve) => { gotDelta = resolve; });
  const stream = new ReadableStream({start(value) { controller = value; }});
  let completed = false;
  const pending = readGenerationStream(response(stream), (name, data) => {
    if (name === 'delta') gotDelta(data.text);
  }).then((data) => { completed = true; return data; });
  controller.enqueue(encode('event: delta\ndata: {"text":"首批正文"}\n\n'));
  assert.equal(await first, '首批正文');
  assert.equal(completed, false);
  controller.enqueue(encode('event: result\ndata: {"done":true}\n\n'));
  assert.deepEqual(await pending, {done: true});
});

test('thinking arrives before content and before completion', async () => {
  let controller;
  let gotThinking;
  const first = new Promise((resolve) => { gotThinking = resolve; });
  const events = [];
  const stream = new ReadableStream({start(value) { controller = value; }});
  let completed = false;
  const pending = readGenerationStream(response(stream), (name, data) => {
    events.push(name);
    if (name === 'thinking') gotThinking(data.text);
  }).then((data) => { completed = true; return data; });
  controller.enqueue(encode('event: thinking\ndata: {"text":"先核对世界规则"}\n\n'));
  assert.equal(await first, '先核对世界规则');
  assert.equal(completed, false);
  controller.enqueue(encode('event: delta\ndata: {"text":"首批正文"}\n\n'));
  controller.enqueue(encode('event: result\ndata: {"done":true}\n\n'));
  await pending;
  assert.deepEqual(events, ['thinking', 'delta', 'result']);
});

test('truncated stream is not reported as success', async () => {
  const stream = new ReadableStream({start(controller) {
    controller.enqueue(encode('event: delta\ndata: {"text":"部分内容"}\n\n'));
    controller.close();
  }});
  await assert.rejects(readGenerationStream(response(stream)), (error) => error.uncertain && /核对/.test(error.message));
});

test('in-stream errors preserve server status and Chinese message', async () => {
  const stream = new ReadableStream({start(controller) {
    controller.enqueue(encode('event: error\ndata: {"message":"模型响应超时","status":504}\n\n'));
    controller.close();
  }});
  await assert.rejects(readGenerationStream(response(stream)), (error) => error.status === 504 && error.message === '模型响应超时');
});

test('partial JSON is rendered as readable Chinese sections and unescaped prose', () => {
  assert.equal(formatGenerationText('{"world":{"title":"云泽","summary":"灵气复苏'), '标题\n云泽\n\n概要\n灵气复苏');
  assert.equal(formatGenerationText('{"content":"第一段\\n第二段\\u4e'), '正文\n第一段\n第二段');
  assert.equal(formatGenerationText('{"verdict":"ready","summary":"没有问题"}'), '审读结论\n可用\n\n概要\n没有问题');
  assert.equal(formatGenerationText('{"content":"<script>危险文本</script>"}'), '正文\n<script>危险文本</script>');
  assert.equal(formatGenerationText('{"world":'), '{"world":');
  assert.equal(formatGenerationText('{"custom_field":"当前分片'), 'custom_field\n当前分片');
  assert.equal(formatGenerationText('{"title":"云泽","custom_field":"新字段'), '标题\n云泽\n\ncustom_field\n新字段');
});

test('view displays thinking and unfinished JSON while the request remains open', async (context) => {
  const frames = new Map();
  let nextFrame = 0;
  const previous = [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame];
  globalThis.requestAnimationFrame = (callback) => { frames.set(++nextFrame, callback); return nextFrame; };
  globalThis.cancelAnimationFrame = (id) => frames.delete(id);
  context.after(() => {
    [globalThis.requestAnimationFrame, globalThis.cancelAnimationFrame] = previous;
  });
  const paint = () => { for (const callback of [...frames.values()]) callback(); };
  const node = () => ({textContent: '', scrollHeight: 100, scrollTop: 0, clientHeight: 100,
    hidden: true, scrollIntoView() {}});
  const output = node();
  const thinkingOutput = node();
  const thinking = {...node(), querySelector: () => thinkingOutput};
  const status = node();
  const nodes = {'.generation-output': output, '.generation-thinking': thinking, '[role="status"]': status};
  const root = {...node(), dataset: {}, setAttribute() {}, querySelector: (selector) => nodes[selector]};
  const view = createGenerationView(root);
  view.begin();

  let controller;
  const received = {};
  const firstThinking = new Promise((resolve) => { received.thinking = resolve; });
  const firstContent = new Promise((resolve) => { received.delta = resolve; });
  const stream = new ReadableStream({start(value) { controller = value; }});
  const pending = readGenerationStream(response(stream), (name, data) => {
    view.event(name, data);
    received[name]?.();
  });
  controller.enqueue(encode('event: thinking\ndata: {"text":"先核对世界规则"}\n\n'));
  await firstThinking;
  paint();
  assert.equal(root.hidden, false);
  assert.equal(thinking.hidden, false);
  assert.equal(thinkingOutput.textContent, '先核对世界规则');
  assert.match(status.textContent, /思考中/);
  assert.equal(root.dataset.state, 'running');

  controller.enqueue(encode('event: delta\ndata: {"text":"{\\"content\\":\\"第一段"}\n\n'));
  await firstContent;
  paint();
  assert.equal(output.textContent, '正文\n第一段');
  assert.equal(root.dataset.state, 'running');
  assert.match(status.textContent, /正在生成/);
  assert.equal(thinkingOutput.textContent, '先核对世界规则');

  controller.enqueue(encode('event: result\ndata: {"id":2}\n\n'));
  await pending;
  view.finish();
  assert.equal(root.dataset.state, 'done');
  view.begin();
  paint();
  assert.equal(thinking.hidden, true);
  assert.equal(thinkingOutput.textContent, '');
  view.event('delta', {text: '{"content":"断流前内容'});
  view.fail('连接中断');
  assert.equal(root.dataset.state, 'error');
  assert.match(output.textContent, /断流前内容/);
  view.hide();
  paint();
  assert.equal(root.hidden, true);
  assert.equal(output.textContent, '');
  assert.equal(thinkingOutput.textContent, '');
});
