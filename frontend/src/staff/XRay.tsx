import { clsx } from "clsx";
import type { Graph } from "./api";
import type { Run } from "./useStaffFeed";

const POS: Record<string, [number, number]> = {
  intent: [170, 26], guard_in: [170, 80], flow: [58, 140], agent: [170, 140], handoff: [282, 140],
  confirm: [58, 200], tools: [170, 200], execute: [58, 260], respond: [226, 260],
};
const NAME: Record<string, string> = {
  intent: "Understand", guard_in: "Guardrails", flow: "Booking flow", agent: "Agent", tools: "Tools",
  confirm: "Patient card", execute: "Execute", handoff: "Handoff", respond: "Reply",
};
const W = 98, H = 30;

/** Curved edges: downward ones leave the bottom and enter the top; back edges loop round the side. */
function edgePath(a: string, b: string): string {
  const [x1, y1] = POS[a]; const [x2, y2] = POS[b];
  if (y1 === y2) {
    const s = Math.sign(x2 - x1);
    return `M${x1 + s * W / 2},${y1} L${x2 - s * W / 2},${y2}`;
  }
  if (y2 > y1) {
    const sy = y1 + H / 2, ty = y2 - H / 2, mid = (sy + ty) / 2;
    return `M${x1},${sy} C${x1},${mid} ${x2},${mid} ${x2},${ty}`;
  }
  const side = x2 <= x1 ? -1 : 1;                       // back edge (e.g. tools → agent, execute → flow)
  const sx = x1 + side * W / 2, tx = x2 + side * W / 2, out = Math.max(sx, tx) * (side > 0 ? 1 : 0) + side * 16;
  const ox = side > 0 ? Math.max(sx, tx) + 16 : Math.min(sx, tx) - 16;
  void out;
  return `M${sx},${y1} C${ox},${y1} ${ox},${y2} ${tx},${y2}`;
}

function fmtMs(ms?: number) { return ms === undefined ? "" : ms >= 1000 ? `${(ms / 1000).toFixed(1)} s` : `${Math.round(ms)} ms`; }
function fmtUsd(v?: number) { return v === undefined ? "" : v === 0 ? "$0" : v < 0.001 ? `$${v.toFixed(5)}` : `$${v.toFixed(4)}`; }

export function XRay({ graph, run, runs, onSelect }: { graph: Graph | null; run: Run | null; runs: Run[]; onSelect: (id: string) => void }) {
  const nodes = (graph?.nodes ?? Object.keys(POS)).filter((n) => POS[n]);
  const edges = (graph?.edges ?? []).filter(([a, b]) => POS[a] && POS[b]);
  const order = run?.visited ?? [];
  const walked = new Set(order.slice(1).map((n, i) => `${order[i]}>${n}`));

  return (
    <section aria-label="Agent X-ray">
      <div className="flex items-baseline justify-between">
        <h2 className="font-semibold">Agent X-ray</h2>
        <p className="text-[12px] text-ink-soft">{graph?.source === "langgraph" ? "Live from the LangGraph workflow" : ""}</p>
      </div>
      <svg viewBox="-20 0 380 290" className="mt-2 w-full" role="img" aria-label={run?.active ? `Running: ${NAME[run.active] ?? run.active}` : "Idle"}>
        <defs>
          <marker id="arr" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" markerHeight="6" orient="auto-start-reverse">
            <path d="M0,0 L8,4 L0,8 z" fill="currentColor" />
          </marker>
        </defs>
        {edges.map(([a, b]) => {
          const on = walked.has(`${a}>${b}`);
          return <path key={`${a}-${b}`} d={edgePath(a, b)} fill="none" markerEnd="url(#arr)"
            className={on ? "text-clinic" : "text-line"} stroke="currentColor" strokeWidth={on ? 2.2 : 1.2} />;
        })}
        {nodes.map((n) => {
          const [x, y] = POS[n];
          const active = run?.active === n;
          const visited = order.includes(n);
          const railed = n === "guard_in" && run?.rail;
          const waiting = n === "confirm" && run?.waiting && run?.finished;
          return (
            <g key={n} transform={`translate(${x - W / 2},${y - H / 2})`}>
              <rect width={W} height={H} rx={9}
                className={clsx(
                  active ? "fill-clinic stroke-clinic-deep" : railed ? (run!.rail!.tone === "alarm" ? "fill-alarm stroke-alarm-deep" : "fill-[#fbefd9] stroke-handoff")
                    : waiting ? "fill-white stroke-stamp" : visited ? "fill-clinic-tint stroke-clinic" : "fill-white stroke-line",
                  active && "pulse-dot",
                )} strokeWidth={active || waiting ? 2 : 1.2} />
              <text x={W / 2} y={H / 2 + 4} textAnchor="middle"
                className={clsx("text-[12px] font-semibold", active || (railed && run!.rail!.tone === "alarm") ? "fill-white" : waiting ? "fill-stamp" : "fill-ink")}>
                {NAME[n] ?? n}
              </text>
            </g>
          );
        })}
      </svg>

      {runs.length > 1 && (
        <div className="mt-1 flex gap-1 overflow-x-auto pb-1" role="tablist" aria-label="Recent turns">
          {runs.map((r, i) => (
            <button key={r.id} role="tab" aria-selected={r.id === run?.id} type="button" onClick={() => onSelect(r.id)}
              className={clsx("shrink-0 rounded-full px-2.5 py-0.5 text-[12px]", r.id === run?.id ? "bg-ink text-white" : "bg-page text-ink-soft hover:text-ink")}>
              {i === 0 ? "Latest" : new Date(r.startedAt).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" })}
            </button>
          ))}
        </div>
      )}

      {run ? (
        <ol className="mt-2 divide-y divide-line/70 text-[13px]">
          {run.steps.map((s, i) => (
            <li key={i} className="grid grid-cols-[1fr_auto] gap-x-3 py-1.5">
              <span className={clsx("min-w-0 truncate", s.tone === "alarm" && "font-semibold text-alarm", s.tone === "handoff" && "font-semibold text-handoff",
                s.tone === "stamp" && "text-stamp", s.ok === false && "text-alarm")}>
                <span className="font-medium">{s.label}</span>{s.detail ? <span className="text-ink-soft">, {s.detail}</span> : null}
              </span>
              <span className="text-right text-ink-soft tabular">{fmtMs(s.ms)}{s.cost !== undefined ? <span className="ml-2">{fmtUsd(s.cost)}</span> : null}</span>
            </li>
          ))}
          {!run.finished && <li className="py-1.5 text-ink-soft">Working…</li>}
        </ol>
      ) : (
        <p className="mt-2 text-[14px] text-ink-soft">Waiting for a patient. Open the patient app and send a message to watch each step light up here.</p>
      )}
    </section>
  );
}
