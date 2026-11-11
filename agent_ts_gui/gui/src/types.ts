// Shared types for the Electron <-> Python backend protocol.

export interface AgentEvent {
  event: string;
  agent_id: string;
  data: Record<string, any>;
  ts: number;
}

export interface ReplyMsg {
  id: number;
  result?: any;
  error?: string;
}

export interface Api {
  getBackendUrl: () => Promise<string | null>;
  getBackendRoot: () => Promise<string | null>;
  getAppVersion: () => Promise<string>;
  onBackendUrl: (cb: (url: string) => void) => void;
  onBackendExit: (cb: (code: number | null) => void) => void;
  send: (payload: object) => Promise<void>;
  onReply: (cb: (msg: ReplyMsg) => void) => void;
  onEvent: (cb: (ev: AgentEvent) => void) => void;
  pickDirectory: () => Promise<string | null>;
  pickFiles: () => Promise<string[]>;
  readFile: (p: string) => Promise<{ content?: string; error?: string }>;
  getLastProject: () => Promise<string | null>;
  setLastProject: (p: string) => Promise<void>;
  restartBackend: (root: string | null) => Promise<void>;
  getConnection: () => Promise<Connection>;
  setConnection: (c: Connection) => Promise<{ ok: boolean; reconnected?: boolean; url?: string; error?: string }>;
  setNativeTheme: (theme: string) => Promise<void>;
  winMinimize: () => Promise<void>;
  winToggleMaximize: () => Promise<boolean>;
  winClose: () => Promise<void>;
}

export interface Connection {
  mode: "local" | "remote";
  host: string;
  port: number;
  token: string;
}

declare global {
  interface Window {
    api: Api;
  }
}

export {};
