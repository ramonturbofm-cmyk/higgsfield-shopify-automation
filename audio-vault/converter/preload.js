const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('omzetter', {
  config: () => ipcRenderer.invoke('config'),
  chooseFolder: (kind) => ipcRenderer.invoke('choose', kind),
  scan: (source, target) => ipcRenderer.invoke('scan', source, target),
  start: (source, target) => ipcRenderer.invoke('start', source, target),
  pause: () => ipcRenderer.invoke('pause'),
  open: (p) => ipcRenderer.invoke('open', p),
  onEvent: (cb) => ipcRenderer.on('event', (e, msg) => cb(msg)),
});
