import { authFetch, wsTicketUrl } from "./auth";

export async function staffGet<T>(path: string, params: Record<string, string> = {}): Promise<T> {
  const qs = new URLSearchParams(params).toString();
  const r = await authFetch(`${path}${qs ? `?${qs}` : ""}`);
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export async function staffPost<T>(path: string, body?: unknown): Promise<T> {
  const r = await authFetch(path, { method: "POST", headers: body ? { "content-type": "application/json" } : {},
                                    body: body ? JSON.stringify(body) : undefined });
  if (!r.ok) throw new Error((await r.json().catch(() => ({}))).detail ?? `HTTP ${r.status}`);
  return r.json() as Promise<T>;
}

export const wsUrl = (after: number): Promise<string> => wsTicketUrl(after);

export interface Doctor { id: string; name: string; specialty: string; booking_style: string; hours: string[][]; on_leave: boolean; now_serving: number | null; waiting: number }
export interface Appt { id: string; doctor_id: string; start: string; end: string; status: string; source: string; token_no: number | null; urgent: boolean; patient: string; type: string | null; booked_by_agent: boolean; in_consult: boolean }
export interface Schedule { day: string; clinic: string; doctors: Doctor[]; appointments: Appt[] }
export interface Stats {
  conversations: number; bookings: number; model_calls: number; cost_usd: number; cost_per_booking_usd: number | null;
  by_tier: Record<string, { calls: number; cost_usd: number; p50_ms: number | null }>;
  latency_by_node: Record<string, { calls: number; p50_ms: number | null; p95_ms: number | null }>;
  guardrails: Record<string, number>; median_turn_ms: number | null;
  redteam: Record<string, { run_at: string; passed: number; total: number; by_category: Record<string, { passed: number; total: number }> }>;
}
export interface Alert { id: string; type: "emergency" | "handoff"; rail: string; detail: string; at: string }
export interface Graph { nodes: string[]; edges: [string, string][]; source: string }
export interface OpsEvent { type: string; run_id: string; thread_id: string; seq: number; cursor: number; ts: string; node: string | null; payload: Record<string, unknown> }
