// Zuinige modus cache: one conversion per track, kept under its size limit.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { createTranscoder } = require('../src/transcode');

const hasFfmpeg = (() => { try { execFileSync('ffmpeg', ['-version']); return true; } catch { return false; } })();

test('MP3 cache converts once, shares parallel requests and removes the least recently used', { skip: !hasFfmpeg && 'ffmpeg not installed' }, async () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'zuinig-'));
  const files = path.join(root, 'files');
  fs.mkdirSync(files);
  const make = (name, f) => execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-f', 'lavfi', '-i', `sine=f=${f}:d=20`, '-ac', '2', '-c:a', 'flac', path.join(files, name)]);
  ['a.flac', 'b.flac', 'c.flac'].forEach((n, i) => make(n, 300 + i * 100));
  const oneMp3 = 20 * 320000 / 8; // 20 s at 320 kbit/s
  const t = createTranscoder({ filesDir: files, cacheDir: path.join(root, 'cache'), maxBytes: oneMp3 * 2.5 });

  const [x, y] = await Promise.all([t.mp3({ storage_key: 'a.flac' }), t.mp3({ storage_key: 'a.flac' })]);
  assert.equal(x, y, 'two requests at once share one conversion');
  assert.ok(fs.statSync(x).size > oneMp3 * 0.9);
  assert.equal(await t.mp3({ storage_key: 'x.mp3' }), null, 'MP3 files are already small');

  await new Promise((r) => setTimeout(r, 20));
  await t.mp3({ storage_key: 'b.flac' });
  await new Promise((r) => setTimeout(r, 20));
  await t.mp3({ storage_key: 'a.flac' }); // a is used again, b is now the oldest
  await new Promise((r) => setTimeout(r, 20));
  await t.mp3({ storage_key: 'c.flac' }); // over the limit → b goes
  const left = fs.readdirSync(path.join(root, 'cache')).sort();
  assert.deepEqual(left, ['a.mp3', 'c.mp3']);
  assert.ok(t.usedBytes <= oneMp3 * 2.5 * 0.9);
  fs.rmSync(root, { recursive: true, force: true });
});
