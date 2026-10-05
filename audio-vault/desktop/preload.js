// Only the app's own local screens (connect, server management) get the `onair`
// bridge; the studio pages from the server only get `onairUpdate` (the update icon),
// which can do nothing but install the newest version from the updates page.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('onairUpdate', {
  check: () => ipcRenderer.invoke('update', 'check'),
  install: () => ipcRenderer.invoke('update', 'install'),
  onStatus: (cb) => ipcRenderer.on('update-status', (e, status) => cb(status)),
});

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
      saveArchive: (values) => ipcRenderer.invoke('server', 'saveArchive', values),
      freeSpace: (dir) => ipcRenderer.invoke('server', 'freeSpace', dir),
      getAutostart: () => ipcRenderer.invoke('server', 'getAutostart'),
      setAutostart: (on) => ipcRenderer.invoke('server', 'setAutostart', on),
      openDockerDownload: () => ipcRenderer.invoke('server', 'openDockerDownload'),
      openStudio: () => ipcRenderer.invoke('server', 'openStudio'),
      databases: () => ipcRenderer.invoke('server', 'databases'),
      switchDatabase: (id) => ipcRenderer.invoke('server', 'switchDatabase', id),
      createDatabase: (name) => ipcRenderer.invoke('server', 'createDatabase', name),
      renameDatabase: (name) => ipcRenderer.invoke('server', 'renameDatabase', name),
      removeDatabase: () => ipcRenderer.invoke('server', 'removeDatabase'),
    },
  });
}
