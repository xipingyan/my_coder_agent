// Preload: type-safe bridge between renderer and main.
import { contextBridge, ipcRenderer } from "electron";

contextBridge.exposeInMainWorld("api", {
  getBackendUrl: (): Promise<string | null> => ipcRenderer.invoke("backend-url"),
  onBackendUrl: (cb: (url: string) => void) => ipcRenderer.on("backend-url", (_e, url) => cb(url)),
  onBackendExit: (cb: (code: number | null) => void) => ipcRenderer.on("backend-exit", (_e, code) => cb(code)),
  restartBackend: (root: string | null): Promise<void> => ipcRenderer.invoke("restart-backend", root),
});
