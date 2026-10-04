// Only the app's own local screens (connect, server management) get this bridge;
// the studio pages from the server never see it.
const { contextBridge, ipcRenderer } = require('electron');

if (location.protocol === 'file:') {
  contextBridge.exposeInMainWorld('onair', {
    config: () => ipcRenderer.invoke('config'),
    connect: (url) => ipcRenderer.invoke('connect', url),
    openServer: () => ipcRenderer.invoke('open-server'),
    onServerLog: (cb) => ipcRenderer.on('server-log', (e, text) => cb(text)),
    server: {
      status: () => ipcRenderer.invoke('server', 'status'),
      saveSettings: (values) => ipcRenderer.invoke('server', 'saveSettings', values),
      chooseFolder: (kind) => ipcRenderer.invoke('server', 'chooseFolder', kind),
      run: (action, arg) => ipcRenderer.invoke('server', 'run', action, arg),
      listBackups: () => ipcRenderer.invoke('server', 'listBackups'),
      startDocker: () => ipcRenderer.invoke('server', 'startDocker'),
      installDocker: () => ipcRenderer.invoke('server', 'installDocker'),
      freeSpace: (dir) => ipcRenderer.invoke('server', 'freeSpace', dir),
      getAutostart: () => ipcRenderer.invoke('server', 'getAutostart'),
      setAutostart: (on) => ipcRenderer.invoke('server', 'setAutostart', on),
      openDockerDownload: () => ipcRenderer.invoke('server', 'openDockerDownload'),
      openStudio: () => ipcRenderer.invoke('server', 'openStudio'),
    },
  });
}
