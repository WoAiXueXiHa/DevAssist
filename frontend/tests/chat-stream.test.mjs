import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';

const dir = await mkdtemp(join(tmpdir(), 'devsupport-sse-'));
const outfile = join(dir, 'api.mjs');
await build({ entryPoints: ['src/api.ts'], outfile, bundle: true, platform: 'browser', format: 'esm' });
const { chatStream, ChatRequestError } = await import(pathToFileURL(outfile));
await rm(dir, { recursive: true });
globalThis.localStorage = { getItem: () => 'test-token' };

function response(text, split = 1) {
  const bytes = new TextEncoder().encode(text);
  return new Response(new ReadableStream({
    start(controller) {
      for (let i = 0; i < bytes.length; i += split) controller.enqueue(bytes.slice(i, i + split));
      controller.close();
    },
  }), { headers: { 'Content-Type': 'text/event-stream' } });
}

test('中文跨字节分块、多行 data、CRLF 和 done 顺序', async () => {
  globalThis.fetch = async () => response('event: meta\r\ndata: {"conversation_id":"c"}\r\n\r\nevent: token\r\ndata: 中文🙂\r\ndata: 第二行\r\n\r\nevent: done\r\ndata: {"answer":"中文🙂\\n第二行"}\r\n\r\n');
  const events = [];
  await chatStream('问', null, { onMeta: () => events.push('meta'), onToken: t => events.push(t), onDone: d => events.push(d.answer) });
  assert.deepEqual(events, ['meta', '中文🙂\n第二行', '中文🙂\n第二行']);
});

test('HTTP 失败显示后端 detail', async () => {
  globalThis.fetch = async () => new Response(JSON.stringify({ detail: 'AI 服务暂不可用' }), { status: 502 });
  await assert.rejects(chatStream('问', null, {}), /AI 服务暂不可用/);
});

test('EOF 缺 done 不伪造成功', async () => {
  globalThis.fetch = async () => response('event: token\ndata: 部分内容\n\n');
  let completed = false;
  await assert.rejects(chatStream('问', null, { onDone: () => completed = true }), /提前结束/);
  assert.equal(completed, false);
});

test('错误 Content-Type 与空响应体', async () => {
  for (const r of [new Response('{}'), new Response(null, { headers: { 'Content-Type': 'text/event-stream' } })]) {
    globalThis.fetch = async () => r;
    await assert.rejects(chatStream('问', null, {}), /格式异常/);
  }
});

test('显式重试保留请求 ID 与原始会话参数，409 提示稍后重试', async () => {
  const bodies = [];
  globalThis.fetch = async (_url, options) => {
    bodies.push(JSON.parse(options.body));
    return bodies.length === 1
      ? new Response(JSON.stringify({ detail: { code: 'chat_request_pending', message: '该请求仍在处理中，请稍后重试。' } }), { status: 409 })
      : response('event: done\ndata: {"answer":"完成"}\n\n');
  };
  await assert.rejects(chatStream('原问题', null, {}, 'same-request-id'), error => error instanceof ChatRequestError && error.status === 409 && /仍在处理中/.test(error.message));
  await chatStream('原问题', null, {}, 'same-request-id');
  assert.deepEqual(bodies, [
    { message: '原问题', conversation_id: null, client_request_id: 'same-request-id' },
    { message: '原问题', conversation_id: null, client_request_id: 'same-request-id' },
  ]);
});


test('结构化 HTTP 错误保留处理中的状态、载荷冲突和终止失败原因', async () => {
  for (const [status, code, message] of [
    [409, 'chat_request_pending', '请求仍在处理中，请稍后重试。'],
    [409, 'chat_request_payload_conflict', '同一个请求标识不能用于不同的消息或会话。'],
    [502, 'chat_request_failed', '本次请求处理失败，系统不会自动重复执行，请查看会话或联系技术支持。'],
  ]) {
    globalThis.fetch = async () => new Response(JSON.stringify({ detail: { code, message } }), { status });
    await assert.rejects(chatStream('问题', null, {}, 'request'), error =>
      error instanceof ChatRequestError && error.status === status && error.code === code && error.message === message);
  }
});
