// Same-origin API helper. All calls are relative; the backend serves the app.

export async function api<T = any>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) {
    let detail = "";
    try {
      detail = (await res.json()).error || res.statusText;
    } catch {
      detail = res.statusText;
    }
    throw new ApiError(res.status, detail || `HTTP ${res.status}`);
  }
  return res.json() as unknown as T;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export interface MarketRow {
  symbol: string;
  name: string;
  category: string;
  active: boolean;
}

export interface TickRow {
  epoch_ms: number;
  quote: number;
  digit: number;
  provider: string;
}

export interface ServerStatus {
  status: string;
  server_time: number;
  connection: Array<{ symbol: string; state: string; latest?: TickRow }>;
  data_source: string;
  oauth_configured: boolean;
  note: string;
}

export const apiGet = <T = any>(p: string) => api<T>(p);
export const apiPost = <T = any>(p: string, body: unknown) =>
  api<T>(p, { method: "POST", body: JSON.stringify(body) });