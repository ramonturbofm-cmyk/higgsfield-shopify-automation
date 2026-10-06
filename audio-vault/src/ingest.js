// Turns an audio file on disk into a row in audio_files: reads the tags, converts
// uncompressed audio (WAV/AIFF) to lossless FLAC when ffmpeg is available, and
// stores the result under STORAGE_DIR/files.
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execFile } = require('child_process');
const mm = require('music-metadata');

const AUDIO_TYPES = {
  '.mp3': 'audio/mpeg', '.wav': 'audio/wav', '.flac': 'audio/flac', '.aac': 'audio/aac',
  '.m4a': 'audio/mp4', '.ogg': 'audio/ogg', '.opus': 'audio/opus', '.aif': 'audio/aiff',
  '.aiff': 'audio/aiff', '.wma': 'audio/x-ms-wma', '.mp2': 'audio/mpeg',
  '.mpeg': 'audio/mpeg', '.mpg': 'audio/mpeg', '.mpga': 'audio/mpeg',
};
// Files named .mpeg/.mpg/.mpga are usually plain MP3s: stored as .mp3 (stream copied,
// no quality loss); anything else inside (MP2, or a video file) is stored as FLAC.
const MPEG_CONTAINERS = new Set(['.mpeg', '.mpg', '.mpga']);
const LOSSLESS_UNCOMPRESSED = new Set(['.wav', '.aif', '.aiff']);

let ffmpegChecked = null;
function hasFfmpeg() {
  if (!ffmpegChecked) {
    ffmpegChecked = new Promise((resolve) => execFile('ffmpeg', ['-version'], (err) => resolve(!err)));
  }
  return ffmpegChecked;
}

// Float and 32-bit WAVs cannot be stored bit-exact in FLAC; those stay as they are.
function notFlacExact(file) {
  return new Promise((resolve) => execFile('ffprobe', ['-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=sample_fmt,bits_per_sample', '-of', 'csv=p=0', file],
    (err, stdout) => {
      const [fmt, bits] = String(stdout).trim().split(',');
      resolve(Boolean(err) || /^(flt|dbl)/.test(fmt || '') || Number(bits) > 24);
    }));
}

function audioCodec(file) {
  return new Promise((resolve) => execFile('ffprobe', ['-v', 'error', '-select_streams', 'a:0', '-show_entries', 'stream=codec_name', '-of', 'csv=p=0', file],
    (err, stdout) => resolve(err ? '' : String(stdout).trim())));
}
function copyToMp3(input, output) {
  return new Promise((resolve, reject) => {
    execFile('ffmpeg', ['-v', 'error', '-y', '-i', input, '-map', '0:a:0', '-map_metadata', '0', '-vn', '-c:a', 'copy', '-f', 'mp3', output],
      { timeout: 10 * 60 * 1000 }, (err, stdout, stderr) => (err ? reject(new Error(stderr || err.message)) : resolve()));
  });
}

function toFlac(input, output) {
  return new Promise((resolve, reject) => {
    execFile('ffmpeg', ['-v', 'error', '-y', '-i', input, '-map', '0:a:0', '-map_metadata', '0', '-c:a', 'flac', '-compression_level', '5', output],
      { timeout: 10 * 60 * 1000 }, (err, stdout, stderr) => (err ? reject(new Error(stderr || err.message)) : resolve()));
  });
}

const convertEnabled = () => process.env.CONVERT_TO_FLAC !== 'false';

/**
 * @param {object} o
 * @param {string} o.source        path of the incoming file
 * @param {boolean} o.move         true: source may be moved/deleted (upload temp file); false: leave it alone (import)
 * @param {string} o.originalName  name shown to people / used for the extension
 */
// Puts an audio file in place under filesDir (converted to FLAC/MP3 where that loses
// nothing) and returns where it went, its stored extension and its tags.
async function storeAudio({ filesDir, source, move, originalName }) {
  const ext = path.extname(originalName).toLowerCase();
  if (!AUDIO_TYPES[ext]) throw Object.assign(new Error(`Geen ondersteund audioformaat: ${originalName}`), { status: 400 });

  let meta = {};
  try { meta = await mm.parseFile(source, { duration: true, skipCovers: true }); } catch { /* unreadable tags are fine */ }

  let storedExt = ext;
  const id = crypto.randomUUID();
  let target = path.join(filesDir, `${id}${ext}`);
  if (LOSSLESS_UNCOMPRESSED.has(ext) && convertEnabled() && (await hasFfmpeg()) && !(await notFlacExact(source))) {
    const flac = path.join(filesDir, `${id}.flac`);
    try {
      await toFlac(source, flac);
      storedExt = '.flac';
      target = flac;
      if (move) fs.rmSync(source, { force: true });
    } catch (err) {
      // e.g. 32-bit float WAV: keep the original rather than losing anything.
      fs.rmSync(flac, { force: true });
      console.warn(`FLAC-conversie mislukt voor ${originalName}, origineel bewaard: ${err.message.trim()}`);
    }
  }
  if (MPEG_CONTAINERS.has(ext) && (await hasFfmpeg())) {
    const mp3 = (await audioCodec(source)) === 'mp3';
    const out = path.join(filesDir, `${id}${mp3 ? '.mp3' : '.flac'}`);
    try {
      await (mp3 ? copyToMp3(source, out) : toFlac(source, out));
      storedExt = mp3 ? '.mp3' : '.flac';
      target = out;
      if (move) fs.rmSync(source, { force: true });
    } catch (err) {
      fs.rmSync(out, { force: true });
      console.warn(`Omzetten mislukt voor ${originalName}, origineel bewaard: ${err.message.trim()}`);
    }
  }
  if (storedExt === ext) {
    if (move) fs.renameSync(source, target); else fs.copyFileSync(source, target);
  }

  return { target, storedExt, meta };
}

async function ingestFile({ pool, filesDir, source, move, originalName, collectionId, userId, overrides = {}, sourcePath = null }) {
  const { target, storedExt, meta } = await storeAudio({ filesDir, source, move, originalName });
  const ext = path.extname(originalName).toLowerCase();
  const common = meta.common || {};
  const title = String(overrides.title || common.title || path.basename(originalName, ext)).trim().slice(0, 300);
  const artist = String(overrides.artist || common.artist || '').trim().slice(0, 300);
  const tags = String(overrides.tags || '').split(',').map((t) => t.trim()).filter(Boolean);
  const genre = [...new Set(common.genre || [])].map((g) => String(g).trim()).filter(Boolean).join(', ').slice(0, 200);
  try {
    const { rows } = await pool.query(
      `INSERT INTO audio_files (collection_id, title, artist, original_name, storage_key, mime_type, size_bytes,
                                duration_seconds, tags, uploaded_by, source_path, genre)
       VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12) RETURNING *`,
      [collectionId, title, artist, originalName, path.basename(target), AUDIO_TYPES[storedExt], fs.statSync(target).size,
        meta.format && meta.format.duration ? meta.format.duration : null, tags, userId, sourcePath, genre]);
    return rows[0];
  } catch (err) {
    fs.rmSync(target, { force: true });
    throw err;
  }
}

// Swap the audio of an existing track for a better version (wrong file, bad quality):
// same id, so playlists, jingle buttons and clocks keep pointing at it. Title and
// artist stay; cue points, loudness and genre are measured again.
async function replaceAudio({ pool, filesDir, file, source, move, originalName }) {
  const { target, storedExt, meta } = await storeAudio({ filesDir, source, move, originalName });
  try {
    const { rows } = await pool.query(
      `UPDATE audio_files SET storage_key = $1, mime_type = $2, size_bytes = $3, duration_seconds = $4, original_name = $5,
              cue_in = NULL, mix_out = NULL, cue_out = NULL, loudness_lufs = NULL, true_peak_db = NULL,
              loudness_checked = FALSE, genre = NULL WHERE id = $6 RETURNING *`,
      [path.basename(target), AUDIO_TYPES[storedExt], fs.statSync(target).size,
        meta.format && meta.format.duration ? meta.format.duration : null, originalName, file.id]);
    fs.rmSync(path.join(filesDir, file.storage_key), { force: true });
    return rows[0];
  } catch (err) {
    fs.rmSync(target, { force: true });
    throw err;
  }
}

module.exports = { AUDIO_TYPES, ingestFile, replaceAudio, hasFfmpeg };
