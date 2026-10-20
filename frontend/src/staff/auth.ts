import { API_URL } from "../realtime/api";

/**
 * Staff session (Day 21). The login token lives in this tab's sessionStorage and is sent as
 * `Authorization: Bearer …` (the app and API are on different sites, so no cookies). It never goes in a URL:
 * the live feed uses a one-time ticket instead.
 */
export interface StaffUser { name: string; role: "owner" | "receptionist" | "doctor"; demo: boolean; doctor_id: string | null; permissions: string[] }

const KEY = "clinicdesk.staff";
let token: string | null = null;
try { token = sessionStorage.getItem(KEY); } catch { /* storage blocked */ }

export const getToken = () => token;
function setToken(t: string | null) {
  token = t;
  try { t ? sessionStorage.setItem(KEY, t) : sessionStorage.removeItem(KEY); } catch { /* storage blocked */ }
}

export class AuthRequired extends Error {}
const listeners = new Set<() => void>();
export const onLoggedOut = (f: () => void) => { listeners.add(f); return () => { listeners.delete(f); }; };

export async function authFetch(path: string, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const r = await fetch(`${API_URL}${path}`, { ...init, headers });
  if (r.status === 401) {
    setToken(null);
    listeners.forEach((f) => f());
    throw new AuthRequired("Please log in again.");
  }
  return r;
}

async function start(path: string, body: unknown): Promise<StaffUser> {
  const r = await fetch(`${API_URL}${path}`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
  const data = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(data.detail ?? `HTTP ${r.status}`);
  setToken(data.token);
  return data.user as StaffUser;
}

export const login = (email: string, password: string) => start("/auth/login", { email, password });
export const demoLogin = (role: StaffUser["role"]) => start("/auth/demo", { role });

export async function me(): Promise<StaffUser | null> {
  if (!token) return null;
  try { return await (await authFetch("/auth/me")).json(); } catch { return null; }
}

export async function logout() {
  try { await authFetch("/auth/logout", { method: "POST" }); } catch { /* already out */ }
  setToken(null);
}

export async function demoEnabled(): Promise<boolean> {
  try { return Boolean((await (await fetch(`${API_URL}/auth/config`)).json()).demo_login); } catch { return false; }
}

export async function wsTicketUrl(after: number): Promise<string> {
  const { ticket } = await (await authFetch("/auth/ws-ticket", { method: "POST" })).json();
  return `${API_URL.replace(/^http/, "ws")}/ops/ws?after=${after}&ticket=${encodeURIComponent(ticket)}`;
}
