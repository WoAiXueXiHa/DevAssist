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
const { chatStream } = await import(pathToFileURL(outfile));
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
