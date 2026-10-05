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

// Appends an "id3 " chunk (ID3v2.3 with title, artist and a front cover) to a WAV file.
function addId3(file, { title, artist, cover }) {
  const frame = (id, body) => { const h = Buffer.alloc(10); h.write(id, 0, 'latin1'); h.writeUInt32BE(body.length, 4); return Buffer.concat([h, body]); };
  const text = (str) => Buffer.concat([Buffer.from([1, 0xff, 0xfe]), Buffer.from(str, 'utf16le')]);
  const frames = Buffer.concat([
    frame('TIT2', text(title)), frame('TPE1', text(artist)),
    frame('APIC', Buffer.concat([Buffer.from([0]), Buffer.from('image/jpeg\0', 'latin1'), Buffer.from([3, 0]), cover])),
  ]);
  const n = frames.length;
  const header = Buffer.from([0x49, 0x44, 0x33, 3, 0, 0, (n >> 21) & 0x7f, (n >> 14) & 0x7f, (n >> 7) & 0x7f, n & 0x7f]);
  let tag = Buffer.concat([header, frames]);
  if (tag.length % 2) tag = Buffer.concat([tag, Buffer.from([0])]);
  const chunk = Buffer.alloc(8); chunk.write('id3 ', 0, 'latin1'); chunk.writeUInt32LE(tag.length, 4);
  const wav = Buffer.concat([fs.readFileSync(file), chunk, tag]);
  wav.writeUInt32LE(wav.length - 8, 4);
  fs.writeFileSync(file, wav);
}

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
  // WAV with ID3 tags and an embedded cover (as written by e.g. Mp3tag).
  ff('-f', 'lavfi', '-i', 'color=c=red:s=64x64', '-frames:v', '1', path.join(root, 'cover.jpg'));
  ff(...tone(700), '-c:a', 'pcm_s16le', path.join(src, 'Pop', 'hoes.wav'));
  addId3(path.join(src, 'Pop', 'hoes.wav'), { title: 'Met Hoes é', artist: 'Hoes Artiest', cover: fs.readFileSync(path.join(root, 'cover.jpg')) });
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
  const probe = (file, entries) => execFileSync('ffprobe', ['-v', 'error', '-show_entries', entries, '-of', 'default=nw=1', file]).toString();
  const hoes = path.join(dst, 'Pop', 'hoes.flac');
  assert.match(probe(hoes, 'format_tags=title,artist'), /title=Met Hoes é[\s\S]*artist=Hoes Artiest|artist=Hoes Artiest[\s\S]*title=Met Hoes é/);
  assert.match(probe(hoes, 'stream_disposition=attached_pic'), /attached_pic=1/, 'embedded cover kept');
  assert.equal(fingerprint(hoes), fingerprint(path.join(src, 'Pop', 'hoes.wav')));

  // Leftover from an interrupted run is cleaned up; finished work is skipped.
  fs.writeFileSync(path.join(dst, 'half.flac.part'), 'x');
  const again = run();
  assert.match(again, /7 al klaar, 0 te doen/);
  assert.ok(!fs.existsSync(path.join(dst, 'half.flac.part')));
  assert.match(fs.readFileSync(path.join(dst, '_omzetrapport.txt'), 'utf8'), /Ruimte totaal/);

  assert.throws(() => execFileSync(process.execPath, [path.join(__dirname, '..', 'src', 'convert-archive.js'), src, path.join(src, 'flac')], { stdio: 'pipe' }),
    /mag niet in de bronmap/);
  fs.rmSync(root, { recursive: true, force: true });
});
