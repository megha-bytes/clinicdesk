import { useEffect, useRef, useState } from "react";
import { type OpsEvent, wsUrl } from "./api";

export interface Step { kind: "node" | "model" | "tool" | "rail" | "card" | "reply" | "error"; label: string; detail?: string; ms?: number; cost?: number; ok?: boolean; tone?: "alarm" | "handoff" | "stamp" }
export interface Run { id: string; startedAt: string; active: string | null; visited: string[]; steps: Step[]; finished: boolean; cost: number; totalMs?: number; rail?: { kind: string; tone: "alarm" | "handoff" }; waiting?: string }

const TIER: Record<string, string> = { lightning: "Nemotron Lightning", nano: "Nemotron Nano", super: "Nemotron Super", ultra: "Nemotron Ultra" };

/** Live staff feed over WebSocket: X-ray runs (latest first) and callbacks for schedule/alerts/stats refreshes. */
export function useStaffFeed(on: { booking?: (e: OpsEvent) => void; alert?: (e: OpsEvent) => void; finished?: () => void }) {
  const [runs, setRuns] = useState<Run[]>([]);
  const [connected, setConnected] = useState(false);
  const cursor = useRef(0);
  const cb = useRef(on);
  cb.current = on;

  useEffect(() => {
    let ws: WebSocket | null = null;
    let stop = false;
    let retry = 0;
    let timer: ReturnType<typeof setTimeout>;

    const apply = (e: OpsEvent) => {
      if (e.cursor) cursor.current = Math.max(cursor.current, e.cursor);
      if (e.type === "booking.changed") cb.current.booking?.(e);
      if (e.type === "emergency" || e.type === "handoff") cb.current.alert?.(e);
      if (e.type === "run.finished") cb.current.finished?.();
      if (e.thread_id === "staff") return;
      setRuns((prev) => {
        const runs = [...prev];
        let i = runs.findIndex((r) => r.id === e.run_id);
        if (i < 0) {
          if (e.type !== "run.started") return prev;
          runs.unshift({ id: e.run_id, startedAt: e.ts, active: null, visited: [], steps: [], finished: false, cost: 0 });
          i = 0;
        }
        const r = { ...runs[i], steps: [...runs[i].steps], visited: [...runs[i].visited] };
        const p = e.payload;
        switch (e.type) {
          case "node.started":
            r.active = e.node;
            if (e.node && !r.visited.includes(e.node)) r.visited.push(e.node);
            break;
          case "node.finished":
            if (r.active === e.node) r.active = null;
            break;
          case "cost":
            r.cost += Number(p.cost_usd ?? 0);
            r.steps.push({ kind: "model", label: TIER[String(p.tier)] ?? String(p.tier), detail: String(p.purpose ?? e.node ?? ""),
                           ms: Number(p.latency_ms ?? 0), cost: Number(p.cost_usd ?? 0), ok: p.ok !== false });
            break;
          case "tool.finished":
            r.steps.push({ kind: "tool", label: String(p.tool), detail: p.ok ? "done" : String(p.error ?? "failed"), ok: Boolean(p.ok) });
            break;
          case "rail.fired": {
            const tone = p.kind === "emergency" ? "alarm" : "handoff";
            r.rail = { kind: String(p.kind), tone };
            r.steps.push({ kind: "rail", label: String(p.rail ?? "guardrail"), detail: `${p.outcome} (${p.engine})`, tone });
            break;
          }
          case "interrupt":
            r.waiting = String(p.kind);
            r.steps.push({ kind: "card", label: p.kind === "consent" ? "Waiting for consent" : "Waiting for the patient's yes", tone: "stamp" });
            break;
          case "run.finished":
            r.finished = true;
            r.active = null;
            r.totalMs = Number(p.total_ms ?? 0);
            if (!p.interrupt) r.steps.push({ kind: "reply", label: "Reply sent", ms: r.totalMs, cost: Number(p.cost_usd ?? r.cost) });
            break;
          case "run.error":
            r.finished = true;
            r.steps.push({ kind: "error", label: "Error", detail: String(p.error ?? "") });
            break;
        }
        runs[i] = r;
        return runs.slice(0, 8);
      });
    };

    const connect = () => {
      if (stop) return;
      ws = new WebSocket(wsUrl(cursor.current));
      ws.onopen = () => { setConnected(true); retry = 0; };
      ws.onmessage = (m) => {
        try {
          const e = JSON.parse(m.data) as OpsEvent;
          if (e.type !== "ping") apply(e);
        } catch { /* ignore */ }
      };
      ws.onclose = () => {
        setConnected(false);
        timer = setTimeout(connect, Math.min(15000, 1000 * 2 ** retry++));
      };
    };
    connect();
    return () => { stop = true; clearTimeout(timer); ws?.close(); };
  }, []);

  return { runs, connected };
}

/** A short two-tone chime for emergencies (WebAudio, no file). Browsers need one click first to allow sound. */
export function chime() {
  try {
    const Ctx = window.AudioContext || (window as unknown as { webkitAudioContext: typeof AudioContext }).webkitAudioContext;
    const ctx = new Ctx();
    [880, 660, 880].forEach((f, i) => {
      const o = ctx.createOscillator();
      const g = ctx.createGain();
      o.frequency.value = f;
      o.type = "sine";
      g.gain.setValueAtTime(0.0001, ctx.currentTime + i * 0.22);
      g.gain.exponentialRampToValueAtTime(0.25, ctx.currentTime + i * 0.22 + 0.02);
      g.gain.exponentialRampToValueAtTime(0.0001, ctx.currentTime + i * 0.22 + 0.2);
      o.connect(g).connect(ctx.destination);
      o.start(ctx.currentTime + i * 0.22);
      o.stop(ctx.currentTime + i * 0.22 + 0.21);
    });
  } catch { /* no audio */ }
}
