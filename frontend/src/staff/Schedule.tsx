import { clsx } from "clsx";
import { Bot, Network, Phone } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { Appt, Doctor, Schedule as ScheduleData } from "./api";

const PX = 2.1; // pixels per minute: a 10-minute visit is ~21 px tall
const toMin = (hhmm: string) => { const [h, m] = hhmm.split(":").map(Number); return h * 60 + m; };
const label = (min: number) => { const h = Math.floor(min / 60); return `${h % 12 || 12}${h < 12 ? "am" : "pm"}`; };

const ACTIONS: Record<string, { action: string; label: string }[]> = {
  confirmed: [{ action: "check-in", label: "Check in" }, { action: "no-show", label: "Mark no-show" }],
  checked_in: [{ action: "start", label: "Start consultation" }, { action: "no-show", label: "Mark no-show" }],
  in_consult: [{ action: "complete", label: "Finish consultation" }],
};

function statusOf(a: Appt) { return a.in_consult ? "in_consult" : a.status; }

function Block({ a, top, height, flash, onAction }: {
  a: Appt; top: number; height: number; flash: boolean; onAction: (a: Appt, action: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const st = statusOf(a);
  const actions = ACTIONS[st] ?? [];
  return (
    <div className="absolute inset-x-1" style={{ top, height }}>
      <button type="button" onClick={() => actions.length && setOpen((o) => !o)} aria-expanded={open}
        aria-label={`${a.start} ${a.patient}, ${st.replace("_", " ")}`}
        className={clsx(
          "flex h-full w-full items-center gap-1 overflow-hidden rounded-[5px] px-1.5 text-left text-[12px] leading-none transition-shadow",
          st === "held" && "border border-dashed border-stamp/60 bg-white text-stamp",
          st === "confirmed" && "border-l-[3px] border-clinic bg-clinic-tint text-ink",
          st === "checked_in" && "border border-clinic bg-white text-ink",
          st === "in_consult" && "bg-clinic text-white",
          st === "completed" && "bg-page text-ink-soft",
          st === "no_show" && "bg-page text-ink-soft line-through",
          flash && "ring-2 ring-clinic ring-offset-1 animate-[pulse_1s_ease-in-out_2]",
        )}>
        <span className="tabular font-semibold">{a.token_no ? `T${a.token_no}` : a.start}</span>
        <span className="min-w-0 flex-1 truncate">{a.patient}</span>
        {a.booked_by_agent && <Bot aria-label="Booked by the assistant" className="h-3.5 w-3.5 shrink-0" />}
        {a.source === "voice" && <Phone aria-label="Booked by phone" className="h-3.5 w-3.5 shrink-0" />}
        {a.source === "a2a" && <Network aria-label="Booked by another agent" className="h-3.5 w-3.5 shrink-0" />}
      </button>
      {open && (
        <div role="menu" className="absolute left-0 top-full z-20 mt-1 w-44 rounded-lg border border-line bg-white p-1 shadow-lg">
          {actions.map((x) => (
            <button key={x.action} role="menuitem" type="button" onClick={() => { setOpen(false); onAction(a, x.action); }}
              className="block w-full rounded-md px-2.5 py-1.5 text-left text-[14px] hover:bg-page">{x.label}</button>
          ))}
        </div>
      )}
    </div>
  );
}

export function Schedule({ data, flash, isToday, onAction }: {
  data: ScheduleData; flash: Set<string>; isToday: boolean; onAction: (a: Appt, action: string) => void;
}) {
  const [lo, hi] = useMemo(() => {
    const times = data.doctors.flatMap((d) => d.hours.flatMap(([a, b]) => [toMin(a), toMin(b)]));
    return times.length ? [Math.floor(Math.min(...times) / 60) * 60, Math.ceil(Math.max(...times) / 60) * 60] : [480, 1200];
  }, [data]);
  const now = new Date();
  const nowMin = now.getHours() * 60 + now.getMinutes();
  const height = (hi - lo) * PX;
  const scroller = useRef<HTMLDivElement>(null);
  useEffect(() => {                                   // open on the current time (or the first booking)
    const first = data.appointments[0] ? toMin(data.appointments[0].start) : lo;
    const target = isToday && nowMin > lo && nowMin < hi ? nowMin - 45 : first - 20;
    scroller.current?.scrollTo({ top: Math.max(0, (target - lo) * PX) });
  }, [data.day]); // eslint-disable-line react-hooks/exhaustive-deps
  const hours = Array.from({ length: (hi - lo) / 60 + 1 }, (_, i) => lo + i * 60);

  return (
    <div ref={scroller} className="max-h-[calc(100dvh-13rem)] min-h-[420px] overflow-auto">
      <div className="grid min-w-[640px]" style={{ gridTemplateColumns: `48px repeat(${data.doctors.length}, minmax(140px, 1fr))` }}>
        <div className="sticky top-0 z-20 bg-white" />
        {data.doctors.map((d) => <DoctorHead key={d.id} d={d} />)}
        <div className="relative" style={{ height }}>
          {hours.map((h) => (
            <span key={h} className="absolute right-2 -translate-y-1/2 text-[11px] text-ink-soft tabular" style={{ top: (h - lo) * PX }}>{label(h)}</span>
          ))}
        </div>
        {data.doctors.map((d) => (
          <div key={d.id} className={clsx("relative border-l border-line", d.on_leave ? "bg-page" : "bg-[repeating-linear-gradient(135deg,#f2f6f5_0_6px,#e9f0ee_6px_7px)]")} style={{ height }}>
            {!d.on_leave && d.hours.map(([a, b]) => (
              <div key={a} className="absolute inset-x-0 bg-white" style={{ top: (toMin(a) - lo) * PX, height: (toMin(b) - toMin(a)) * PX }} />
            ))}
            {hours.map((h) => <div key={h} className="absolute inset-x-0 border-t border-line/60" style={{ top: (h - lo) * PX }} />)}
            {d.on_leave && <p className="absolute inset-x-0 top-6 text-center text-[13px] text-ink-soft">On leave</p>}
            {data.appointments.filter((a) => a.doctor_id === d.id).map((a) => (
              <Block key={a.id} a={a} flash={flash.has(a.id)} onAction={onAction}
                top={(toMin(a.start) - lo) * PX + 1} height={Math.max(16, (toMin(a.end) - toMin(a.start)) * PX - 2)} />
            ))}
            {isToday && nowMin > lo && nowMin < hi && (
              <div aria-hidden className="absolute inset-x-0 z-10 border-t-2 border-clinic" style={{ top: (nowMin - lo) * PX }} />
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function DoctorHead({ d }: { d: Doctor }) {
  return (
    <div className="sticky top-0 z-20 border-b border-l border-line bg-white px-2 pb-2">
      <p className="truncate font-semibold leading-tight">{d.name}</p>
      <p className="truncate text-[13px] text-ink-soft leading-tight">{d.specialty}</p>
      <p className="mt-1 text-[12px] text-ink-soft tabular">
        {d.booking_style === "token"
          ? <>Now serving <b className="text-ink">{d.now_serving ? `T${d.now_serving}` : "none"}</b>, {d.waiting} waiting</>
          : <>{d.waiting} still to see</>}
      </p>
    </div>
  );
}
