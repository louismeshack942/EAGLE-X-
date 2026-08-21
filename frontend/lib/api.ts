/**
 * EAGLE-X API client.
 *
 * The backend serves this frontend from the SAME origin (single service),
 * so every request is a plain relative fetch: /health, /status, /intelligence/...
 * No proxy, no build-time URL, nothing to misconfigure.
 *
 * NEXT_PUBLIC_BACKEND_URL, if set at build time, overrides the base — useful
 * only if the frontend is ever hosted separately from the backend.
 */

const BASE = (process.env.NEXT_PUBLIC_BACKEND_URL || "").replace(/\/$/, "");

/** Same-origin base for full-page navigations (OAuth login, downloads). */
export const API_BASE = BASE;

function join(path: string) {
  return `${BASE}${path.startsWith("/") ? path : `/${path}`}`;
}

export async function apiGet<T = unknown>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(join(path), { cache: "no-store", ...init });
  if (!res.ok) throw new Error(`GET ${path} -> ${res.status}`);
  return res.json();
}

export async function apiPost<T = unknown>(path: string, body?: unknown, init?: RequestInit): Promise<T> {
  const res = await fetch(join(path), {
    method: "POST",
    headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    body: body ? JSON.stringify(body) : undefined,
    ...init,
  });
  if (!res.ok) throw new Error(`POST ${path} -> ${res.status}`);
  return res.json();
}

export async function apiPatch<T = unknown>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(join(path), {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`PATCH ${path} -> ${res.status}`);
  return res.json();
}

export async function apiDel<T = unknown>(path: string): Promise<T> {
  const res = await fetch(join(path), { method: "DELETE" });
  if (!res.ok) throw new Error(`DELETE ${path} -> ${res.status}`);
  return res.json();
}

/** Legacy object-style wrapper kept for compatibility. */
export const api = {
  get: apiGet,
  post: apiPost,
  patch: apiPatch,
  del: apiDel,
};

export function fmtUsd(n: number | null | undefined, opts?: Intl.NumberFormatOptions): string {
  if (n === null || n === undefined) return "$0.00";
  return new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    minimumFractionDigits: 2,
    ...(opts ?? {}),
  }).format(n);
}
