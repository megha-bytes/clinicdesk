import { clsx } from "clsx";
import { ChevronLeft, ChevronRight, LogOut, Volume2, VolumeX } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type Alert, type Appt, type Graph, type Schedule as ScheduleData, type Stats, staffGet, staffPost } from "./api";
import { Alerts, Numbers } from "./Panels";
import { Schedule } from "./Schedule";
import { chime, useStaffFeed } from "./useStaffFeed";
import { XRay } from "./XRay";
import { demoEnabled, demoLogin, logout, me, onLoggedOut, type StaffUser } from "./auth";
import { Login } from "./Login";
import { Onboarding } from "./Onboarding";

const EMBEDDED = typeof window !== "undefined" && window.self !== window.top;   // inside the judge page
const ROLE_LABEL: Record<StaffUser["role"], string> = { owner: "Owner", receptionist: "Receptionist", doctor: "Doctor" };

/** Staff login gate. `/staff?demo=receptionist` (used by the judge page) opens a one-click demo session. */
export default function StaffApp() {
  const [user, setUser] = useState<StaffUser | null | undefined>(undefined);
  useEffect(() => {
    const role = new URLSearchParams(window.location.search).get("demo") as StaffUser["role"] | null;
    (async () => {
      const u = await me();
      if (u && (!role || u.role === role)) return setUser(u);
      if (role && ["owner", "receptionist", "doctor"].includes(role) && await demoEnabled()) {
        try { return setUser(await demoLogin(role)); } catch { /* fall through to the login page */ }
      }
      setUser(null);
    })();
    return onLoggedOut(() => setUser(null));
  }, []);
  if (user === undefined) return <p className="p-8 text-ink-soft">Loading…</p>;
  if (user === null) return <Login onIn={setUser} />;
  return <Dashboard user={user} onLogout={async () => { await logout(); setUser(null); }} />;
}

const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;

function Dashboard({ user, onLogout }: { user: StaffUser; onLogout: () => void }) {
  const can = (p: string) => user.permissions.includes(p);
  const [day, setDay] = useState(() => iso(new Date()));
  const [schedule, setSchedule] = useState<ScheduleData | null>(null);
  const [stats, setStats] = useState<Stats | null>(null);
  const [alerts, setAlerts] = useState<Alert[]>([]);
  const [handled, setHandled] = useState<Set<string>>(new Set());
  const [graph, setGraph] = useState<Graph | null>(null);
  const [flash, setFlash] = useState<Set<string>>(new Set());
  const [sound, setSound] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<{ text: string; date: string } | null>(null);
  const dayRef = useRef(day);
  dayRef.current = day;
  const soundRef = useRef(sound);
  soundRef.current = sound;

  const loadSchedule = useCallback(() => staffGet<ScheduleData>("/ops/schedule", { day }).then(setSchedule).catch(() => setError("Couldn't load the schedule.")), [day]);
  const loadStats = useCallback(() => staffGet<Stats>("/ops/stats").then(setStats).catch(() => {}), []);
  const loadAlerts = useCallback(() => staffGet<{ alerts: Alert[] }>("/ops/alerts").then((r) => setAlerts(r.alerts)).catch(() => {}), []);

  useEffect(() => { loadSchedule(); }, [loadSchedule]);
  useEffect(() => {
    loadAlerts();
    if (user.permissions.includes("stats")) { loadStats(); staffGet<Graph>("/ops/graph").then(setGraph).catch(() => {}); }
  }, [loadStats, loadAlerts, user]);

  const { runs, connected } = useStaffFeed({
    booking: (e) => {
      const id = String(e.payload.appointment_id ?? "");
      const date = String(e.payload.date ?? "");
      loadSchedule();
      if (date && date !== dayRef.current && e.payload.action === "book") {
        setNotice({ text: `New booking: ${e.payload.doctor}, ${e.payload.when}`, date });
      }
      if (id) {
        setFlash((f) => new Set(f).add(id));
        setTimeout(() => setFlash((f) => { const n = new Set(f); n.delete(id); return n; }), 4000);
      }
    },
    alert: (e) => { loadAlerts(); if (e.type === "emergency" && soundRef.current) chime(); },
    finished: () => { if (user.permissions.includes("stats")) loadStats(); },
  });

  const run = useMemo(() => runs.find((r) => r.id === selected) ?? runs[0] ?? null, [runs, selected]);
  useEffect(() => { if (runs[0] && !runs[0].finished) setSelected(null); }, [runs]);

  const act = async (a: Appt, action: string) => {
    try { await staffPost(`/ops/appointments/${a.id}/${action}`); setError(null); }
    catch (e) { setError(e instanceof Error ? e.message : "That didn't work."); }
  };
  const shift = (n: number) => { const d = new Date(`${day}T12:00:00`); d.setDate(d.getDate() + n); setDay(iso(d)); };
  const isToday = day === iso(new Date());
  const visibleAlerts = alerts.filter((a) => !handled.has(a.id));
  const dateLabel = new Date(`${day}T12:00:00`).toLocaleDateString([], { weekday: "long", day: "numeric", month: "long" });

  return (
    <div className="min-h-full bg-page">
      <header className="flex flex-wrap items-center gap-x-6 gap-y-2 border-b border-line bg-white px-5 py-3">
        <div className="flex items-center gap-3">
          <div aria-hidden className="grid h-9 w-9 place-items-center rounded-xl bg-clinic">
            <svg viewBox="0 0 32 32" className="h-5 w-5"><path d="M13 8h6v5h5v6h-5v5h-6v-5H8v-6h5z" fill="#F2F6F5" /></svg>
          </div>
          <div>
            <p className="font-semibold leading-tight">{schedule?.clinic ?? "Sunrise Family Clinic"}</p>
            <p className="text-[13px] leading-tight text-ink-soft">Front desk dashboard</p>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button type="button" onClick={() => shift(-1)} aria-label="Previous day" className="grid h-8 w-8 place-items-center rounded-full hover:bg-page"><ChevronLeft className="h-4 w-4" /></button>
          <p className="min-w-44 text-center font-medium">{dateLabel}</p>
          <button type="button" onClick={() => shift(1)} aria-label="Next day" className="grid h-8 w-8 place-items-center rounded-full hover:bg-page"><ChevronRight className="h-4 w-4" /></button>
          {!isToday && <button type="button" onClick={() => setDay(iso(new Date()))} className="ml-1 rounded-full px-3 py-1 text-[13px] text-clinic-deep hover:bg-clinic-tint">Today</button>}
        </div>
        <div className="ml-auto flex items-center gap-4">
          <p className="flex items-center gap-2 text-[13px] text-ink-soft" aria-live="polite">
            <span className={clsx("h-2 w-2 rounded-full", connected ? "bg-clinic" : "bg-handoff")} />
            {connected ? "Live" : "Reconnecting…"}
          </p>
          <button type="button" onClick={() => { setSound((s) => !s); if (!sound) chime(); }} aria-pressed={sound}
            className="flex items-center gap-1.5 rounded-full px-3 py-1.5 text-[13px] hover:bg-page">
            {sound ? <Volume2 className="h-4 w-4" /> : <VolumeX className="h-4 w-4" />} {sound ? "Alert sound on" : "Turn on alert sound"}
          </button>
          <div className="flex items-center gap-2 border-l border-line pl-4">
            <p className="text-right text-[13px] leading-tight">
              <span className="block font-semibold">{user.name}</span>
              <span className="text-ink-soft">{ROLE_LABEL[user.role]}{user.demo ? " · demo" : ""}</span>
            </p>
            <button type="button" onClick={onLogout} aria-label="Log out" title="Log out" className="grid h-8 w-8 place-items-center rounded-full text-ink-soft hover:bg-page hover:text-ink">
              <LogOut className="h-4 w-4" />
            </button>
          </div>
        </div>
      </header>

      <div className={clsx("grid gap-5 p-5", can("xray") && (EMBEDDED ? "grid-cols-[minmax(0,1fr)_330px] gap-4 p-4" : "xl:grid-cols-[minmax(0,1fr)_380px]"))}>
        <main className="min-w-0 space-y-4">
          <Alerts alerts={visibleAlerts} onDismiss={(id) => setHandled((h) => new Set(h).add(id))} />
          {notice && (
            <div role="status" className="flex items-center gap-3 rounded-xl border border-clinic/30 bg-clinic-tint px-4 py-2.5">
              <p className="flex-1 text-[15px]">{notice.text}</p>
              <button type="button" onClick={() => { setDay(notice.date); setNotice(null); }}
                className="rounded-lg bg-clinic px-3 py-1.5 text-[14px] font-semibold text-white hover:bg-clinic-deep">Show that day</button>
              <button type="button" onClick={() => setNotice(null)} className="rounded-lg px-2 py-1.5 text-[14px] text-ink-soft hover:bg-white">Dismiss</button>
            </div>
          )}
          {error && <p role="alert" className="rounded-lg bg-alarm/10 px-3 py-2 text-[14px] text-alarm-deep">{error}</p>}
          <div className="rounded-2xl border border-line bg-white p-3">
            {schedule ? <Schedule data={schedule} flash={flash} isToday={isToday} onAction={act} /> : <p className="p-6 text-ink-soft">Loading the schedule…</p>}
          </div>
          <p className="text-[12px] text-ink-soft">
            {user.role === "doctor" ? "Your patients only. Click a booking to start or finish the visit." : "Click a booking to check the patient in or update it."}
            {user.demo ? " Demo session: patient names are shortened." : ""} Phone numbers are never shown here.
          </p>
          {can("onboarding") && <Onboarding />}
        </main>
        {can("xray") && (
          <aside className="space-y-6 rounded-2xl border border-line bg-white p-4 xl:self-start">
            <XRay graph={graph} run={run} runs={runs} onSelect={setSelected} />
            <Numbers s={stats} />
          </aside>
        )}
      </div>
    </div>
  );
}
