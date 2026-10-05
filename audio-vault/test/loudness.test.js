// Loudness measurement (EBU R128) used for "Gelijk volume" in the studio.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');
const { measure } = require('../src/loudness');

const hasFfmpeg = (() => { try { execFileSync('ffmpeg', ['-version']); return true; } catch { return false; } })();

test('measures loudness and true peak; a 12 dB quieter track measures 12 LU lower', { skip: !hasFfmpeg && 'ffmpeg not installed' }, async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'loud-'));
  const make = (name, volume) => execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-f', 'lavfi', '-i', 'sine=f=1000:d=10',
    '-af', `volume=${volume}`, '-ac', '2', '-c:a', 'flac', path.join(dir, name)]);
  make('loud.flac', '0dB');
  make('quiet.flac', '-12dB');
  const loud = await measure(path.join(dir, 'loud.flac'));
  const quiet = await measure(path.join(dir, 'quiet.flac'));
  assert.ok(Math.abs(loud.lufs - quiet.lufs - 12) < 0.3, `difference ${loud.lufs - quiet.lufs}`);
  assert.ok(Math.abs(loud.peak - quiet.peak - 12) < 0.3);
  assert.ok(loud.peak < -15 && loud.peak > -24, 'test tone is well below full scale');
  await assert.rejects(measure(path.join(dir, 'missing.flac')));
  fs.rmSync(dir, { recursive: true, force: true });
});
