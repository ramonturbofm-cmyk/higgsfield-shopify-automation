// Only the local connect screen gets this small bridge; the studio pages from the
// server never see it.
const { contextBridge, ipcRenderer } = require('electron');

if (location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('onair', {
    config: () => ipcRenderer.invoke('config'),
    connect: (url) => ipcRenderer.invoke('connect', url),
  });
}
