import { clsx } from "clsx";
import type { Alert, Stats } from "./api";

function ago(iso: string) {
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  return `${Math.floor(s / 3600)} h ago`;
}

const KIND: Record<string, string> = {
  cardiac: "chest pain", breathing: "breathing trouble", stroke: "stroke signs", unconscious: "unconscious",
  bleeding: "heavy bleeding", poisoning: "poisoning", seizure: "seizure", injury: "serious injury",
  self_harm: "self-harm risk", reported: "reported by the patient",
};

export function Alerts({ alerts, onDismiss }: { alerts: Alert[]; onDismiss: (id: string) => void }) {
  if (!alerts.length) return null;
  return (
    <section aria-label="Alerts" aria-live="assertive" className="space-y-2">
      {alerts.map((a) => {
        const em = a.type === "emergency";
        const kind = a.detail.split(": ").pop() ?? "";
        return (
          <div key={a.id} className={clsx("flex items-start gap-3 rounded-xl px-4 py-3", em ? "bg-alarm text-white" : "border-l-4 border-handoff bg-[#fbefd9] text-ink")}>
            <div className="min-w-0 flex-1">
              <p className="font-semibold leading-tight">{em ? `Emergency: ${KIND[kind] ?? "check the conversation"}` : "A patient asked for a call back"}</p>
              <p className={clsx("text-[13px]", em ? "text-white/85" : "text-ink-soft")}>
                {em ? "The patient was told to call 108/112. " : ""}{ago(a.at)}
              </p>
            </div>
            <button type="button" onClick={() => onDismiss(a.id)}
              className={clsx("shrink-0 rounded-lg px-2.5 py-1 text-[13px] font-semibold", em ? "bg-white/15 hover:bg-white/25" : "hover:bg-white")}>
              Mark handled
            </button>
          </div>
        );
      })}
    </section>
  );
}

function usd(v: number | null | undefined) {
  if (v === null || v === undefined) return "none yet";
  return v < 0.01 ? `$${v.toFixed(5)}` : `$${v.toFixed(3)}`;
}

export function Numbers({ s }: { s: Stats | null }) {
  if (!s) return null;
  const rails = Object.entries(s.guardrails).filter(([k]) => k !== "handoff");
  const blocked = rails.reduce((n, [, v]) => n + v, 0);
  const rt = s.redteam.live ?? s.redteam.scripted;
  const rows: [string, React.ReactNode][] = [
    ["Bookings by the assistant", s.bookings],
    ["Conversations", s.conversations],
    ["Model cost per booking", usd(s.cost_per_booking_usd)],
    ["Model cost today", usd(s.cost_usd)],
    ["Typical model call", s.median_turn_ms ? `${(s.median_turn_ms / 1000).toFixed(1)} s` : "none yet"],
    ["Guardrail decisions", blocked ? `${blocked} (${rails.map(([k, v]) => `${v} ${k.replace("output_", "reply ")}`).join(", ")})` : "none today"],
    ["Red-team suite", rt ? `${rt.passed} / ${rt.total} passed${s.redteam.live ? " (live models)" : " (scripted)"}` : "not run"],
  ];
  return (
    <section aria-label="Today">
      <h2 className="font-semibold">Today</h2>
      <dl className="mt-2 divide-y divide-line/70 text-[14px]">
        {rows.map(([k, v]) => (
          <div key={k} className="flex justify-between gap-4 py-1.5">
            <dt className="text-ink-soft">{k}</dt>
            <dd className="text-right font-medium tabular">{v}</dd>
          </div>
        ))}
      </dl>
      {Object.keys(s.by_tier).length > 0 && (
        <div className="mt-3 text-[13px]">
          <p className="text-ink-soft">By model</p>
          {Object.entries(s.by_tier).map(([tier, t]) => (
            <p key={tier} className="flex justify-between tabular">
              <span>Nemotron {tier[0].toUpperCase() + tier.slice(1)}</span>
              <span className="text-ink-soft">{t.calls} calls, {t.p50_ms ? `${(t.p50_ms / 1000).toFixed(1)} s typical` : ""}, {usd(t.cost_usd)}</span>
            </p>
          ))}
        </div>
      )}
    </section>
  );
}
