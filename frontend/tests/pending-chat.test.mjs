import { test } from 'node:test';
import assert from 'node:assert/strict';
import { build } from 'esbuild';
import { mkdtemp, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { pathToFileURL } from 'node:url';
import { webcrypto } from 'node:crypto';

globalThis.crypto ??= webcrypto;
const dir = await mkdtemp(join(tmpdir(), 'devsupport-pending-'));
const outfile = join(dir, 'pending.mjs');
await build({ entryPoints: ['src/pendingChat.ts'], outfile, bundle: true, platform: 'browser', format: 'esm' });
const { abandonPending, newPendingChat, pendingKey, readPending, savePending } = await import(pathToFileURL(outfile));
await rm(dir, { recursive: true });

const rows = new Map();
const storage = { getItem: key => rows.get(key) ?? null, setItem: (key, value) => rows.set(key, value), removeItem: key => rows.delete(key) };

test('刷新恢复同一请求，按租户和用户隔离，完成后清理', () => {
  const key = pendingKey({ tenant_id: 'tenant', user_id: 'user' });
  const other = pendingKey({ tenant_id: 'other', user_id: 'user' });
  const request = newPendingChat('问题', null);
  savePending(storage, key, request);
  assert.deepEqual(readPending(storage, key), request);
  assert.equal(readPending(storage, other), null);
  assert.notEqual(newPendingChat('问题', null).id, request.id);
  savePending(storage, key, null);
  assert.equal(readPending(storage, key), null);
});

test('原请求不携带新 meta 会话，非法存储不能恢复', () => {
  const request = newPendingChat('问题', 'original-conv');
  savePending(storage, 'key', request);
  assert.equal(readPending(storage, 'key').conversationId, 'original-conv');
  storage.setItem('key', '{invalid');
  assert.equal(readPending(storage, 'key'), null);
});


test('未确认完成保留请求，只有显式结束才移除', () => {
  const request = newPendingChat('服务失败后的原问题', null);
  savePending(storage, 'failed', request);
  // Failures do not invoke completion or abandonment.
  assert.deepEqual(readPending(storage, 'failed'), request);
  abandonPending(storage, 'failed');
  assert.equal(readPending(storage, 'failed'), null);
});
