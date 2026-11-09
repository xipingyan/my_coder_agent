// Preload: type-safe bridge between renderer and main.
import { contextBridge, ipcRenderer } from "electron";
import type { AgentEvent, ReplyMsg } from "../types";

contextBridge.exposeInMainWorld("api", {
  getBackendUrl: (): Promise<string | null> => ipcRenderer.invoke("backend-url"),
  getBackendRoot: (): Promise<string | null> => ipcRenderer.invoke("backend-root"),
  onBackendUrl: (cb: (url: string) => void) => ipcRenderer.on("backend-url", (_e, url) => cb(url)),
  onBackendExit: (cb: (code: number | null) => void) => ipcRenderer.on("backend-exit", (_e, code) => cb(code)),
  send: (payload: object): Promise<void> => ipcRenderer.invoke("ws-send", payload),
  onReply: (cb: (msg: ReplyMsg) => void) => ipcRenderer.on("agent-reply", (_e, msg) => cb(msg)),
  onEvent: (cb: (ev: AgentEvent) => void) => ipcRenderer.on("agent-event", (_e, ev) => cb(ev)),
  pickDirectory: (): Promise<string | null> => ipcRenderer.invoke("pick-directory"),
  pickFiles: (): Promise<string[]> => ipcRenderer.invoke("pick-files"),
  readFile: (p: string): Promise<{ content?: string; error?: string }> => ipcRenderer.invoke("read-file", p),
  getLastProject: (): Promise<string | null> => ipcRenderer.invoke("last-project"),
  setLastProject: (p: string): Promise<void> => ipcRenderer.invoke("set-last-project", p),
  restartBackend: (root: string | null): Promise<void> => ipcRenderer.invoke("restart-backend", root),
  getConnection: (): Promise<{ mode: string; host: string; port: number; token: string }> => ipcRenderer.invoke("get-connection"),
  setConnection: (c: { mode: string; host: string; port: number; token: string }): Promise<{ ok: boolean; reconnected?: boolean; url?: string; error?: string }> => ipcRenderer.invoke("set-connection", c),
});
