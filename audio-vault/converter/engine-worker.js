// Runs scanning and converting off the window's thread, so a large network folder
// never freezes the program.
const { parentPort, workerData } = require('worker_threads');
const { convertArchive, scanArchive } = require('./lib/convert-archive');

const { mode, source, target, jobs, ffmpeg, ffprobe } = workerData;
const send = (msg) => parentPort.postMessage(msg);

if (mode === 'scan') {
  try { send({ type: 'scan', result: scanArchive(source, target) }); } catch (err) { send({ type: 'error', message: err.message }); }
} else {
  const controller = new AbortController();
  parentPort.on('message', (m) => { if (m === 'stop') controller.abort(); });
  convertArchive({ source, target, jobs, ffmpeg, ffprobe, signal: controller.signal, onEvent: send })
    .catch((err) => send({ type: 'error', message: err.message }));
}
