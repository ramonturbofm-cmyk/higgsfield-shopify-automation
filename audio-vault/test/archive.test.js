// "Archief omzetten": every FLAC must hold exactly the same audio as its WAV, files
// FLAC cannot hold exactly are kept, other files are copied, re-running skips work.
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('fs');
const os = require('os');
const path = require('path');
const { execFileSync } = require('child_process');

const hasFfmpeg = (() => { try { execFileSync('ffmpeg', ['-version']); execFileSync('ffprobe', ['-version']); return true; } catch { return false; } })();
const ff = (...args) => execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-y', ...args]);
const fingerprint = (file) => /MD5=(\w+)/.exec(execFileSync('ffmpeg', ['-nostdin', '-v', 'error', '-i', file, '-map', '0:a', '-c:a', 'pcm_s32le', '-f', 'md5', '-']).toString())[1];

test('archive conversion is bit-exact, keeps what FLAC cannot hold and resumes', { skip: !hasFfmpeg && 'ffmpeg not installed' }, () => {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'archief-'));
  const src = path.join(root, 'bron');
  const dst = path.join(root, 'doel');
  fs.mkdirSync(path.join(src, 'Pop', 'Artiest'), { recursive: true });
  fs.mkdirSync(path.join(src, '@eaDir'), { recursive: true });
  const tone = (f) => ['-f', 'lavfi', '-i', `sine=f=${f}:d=2`, '-ac', '2'];
  ff(...tone(440), '-c:a', 'pcm_s16le', '-metadata', 'title=Zomer', path.join(src, 'Pop', 'Artiest', 'zomer.wav'));
  ff(...tone(500), '-ar', '48000', '-c:a', 'pcm_s24le', path.join(src, 'Pop', '24bit.wav'));
  ff(...tone(550), '-c:a', 'pcm_s16be', path.join(src, 'id.aiff'));
  ff(...tone(600), '-c:a', 'pcm_s32le', path.join(src, '32bit.wav'));
  ff(...tone(650), '-c:a', 'pcm_f32le', path.join(src, 'float.wav'));
  fs.writeFileSync(path.join(src, 'Pop', 'cover.jpg'), 'jpg');
  fs.writeFileSync(path.join(src, '@eaDir', 'thumb.wav'), 'synology junk');

  const run = () => execFileSync(process.execPath, [path.join(__dirname, '..', 'src', 'convert-archive.js'), src, dst]).toString();
  assert.match(run(), /Mislukt: 0/);

  for (const [from, to] of [['Pop/Artiest/zomer.wav', 'Pop/Artiest/zomer.flac'], ['Pop/24bit.wav', 'Pop/24bit.flac'], ['id.aiff', 'id.flac']]) {
    assert.equal(fingerprint(path.join(dst, to)), fingerprint(path.join(src, from)), `${to} is bit-exact`);
  }
  // 32-bit and float WAV cannot be stored exactly in FLAC: kept untouched.
  assert.deepEqual(fs.readFileSync(path.join(dst, '32bit.wav')), fs.readFileSync(path.join(src, '32bit.wav')));
  assert.deepEqual(fs.readFileSync(path.join(dst, 'float.wav')), fs.readFileSync(path.join(src, 'float.wav')));
  assert.ok(!fs.existsSync(path.join(dst, '32bit.flac')) && !fs.existsSync(path.join(dst, 'float.flac')));
  assert.equal(fs.readFileSync(path.join(dst, 'Pop', 'cover.jpg'), 'utf8'), 'jpg');
  assert.ok(!fs.existsSync(path.join(dst, '@eaDir')), 'Synology system folders are skipped');
  assert.match(execFileSync('ffprobe', ['-v', 'error', '-show_entries', 'format_tags=title', '-of', 'csv=p=0', path.join(dst, 'Pop/Artiest/zomer.flac')]).toString(), /Zomer/);
  assert.ok(fs.existsSync(src + '/Pop/Artiest/zomer.wav'), 'source untouched');

  // Leftover from an interrupted run is cleaned up; finished work is skipped.
  fs.writeFileSync(path.join(dst, 'half.flac.part'), 'x');
  const again = run();
  assert.match(again, /6 al klaar, 0 te doen/);
  assert.ok(!fs.existsSync(path.join(dst, 'half.flac.part')));
  assert.match(fs.readFileSync(path.join(dst, '_omzetrapport.txt'), 'utf8'), /Ruimte totaal/);

  assert.throws(() => execFileSync(process.execPath, [path.join(__dirname, '..', 'src', 'convert-archive.js'), src, path.join(src, 'flac')], { stdio: 'pipe' }),
    /mag niet in de bronmap/);
  fs.rmSync(root, { recursive: true, force: true });
});
