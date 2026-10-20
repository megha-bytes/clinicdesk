import { LogIn, Stethoscope, UserRound, Building2 } from "lucide-react";
import { useEffect, useState } from "react";
import { demoEnabled, demoLogin, login, type StaffUser } from "./auth";

const DEMO: { role: StaffUser["role"]; title: string; what: string; Icon: typeof UserRound }[] = [
  { role: "receptionist", title: "Receptionist", what: "Whole schedule, check-ins, alerts", Icon: UserRound },
  { role: "doctor", title: "Doctor", what: "Dr. Ananya Rao's patients only", Icon: Stethoscope },
  { role: "owner", title: "Owner", what: "Everything, plus Agent X-ray, costs, onboarding", Icon: Building2 },
];

export function Login({ onIn }: { onIn: (u: StaffUser) => void }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [demo, setDemo] = useState(false);
  useEffect(() => { demoEnabled().then(setDemo); }, []);

  const go = async (f: () => Promise<StaffUser>) => {
    setBusy(true); setError(null);
    try { onIn(await f()); } catch (e) { setError(e instanceof Error ? e.message : "Couldn't log in."); } finally { setBusy(false); }
  };

  return (
    <div className="grid min-h-full place-items-center bg-page px-4 py-10">
      <div className="w-full max-w-[440px]">
        <div className="mb-6 flex items-center gap-3">
          <div aria-hidden className="grid h-11 w-11 place-items-center rounded-xl bg-clinic">
            <svg viewBox="0 0 32 32" className="h-6 w-6"><path d="M13 8h6v5h5v6h-5v5h-6v-5H8v-6h5z" fill="#F2F6F5" /></svg>
          </div>
          <div>
            <h1 className="text-xl font-semibold leading-tight">Front desk dashboard</h1>
            <p className="text-[14px] text-ink-soft">Staff sign in</p>
          </div>
        </div>

        {demo && (
          <section className="mb-5 rounded-2xl border-2 border-clinic/30 bg-white p-5" aria-label="Try the demo">
            <h2 className="font-semibold">Try the demo clinic</h2>
            <p className="mt-1 text-[14px] text-ink-soft">One click, no password. Each role sees only what it should.</p>
            <div className="mt-4 grid gap-2">
              {DEMO.map(({ role, title, what, Icon }) => (
                <button key={role} type="button" disabled={busy} onClick={() => go(() => demoLogin(role))}
                  className="flex items-center gap-3 rounded-xl border border-line px-4 py-3 text-left hover:border-clinic hover:bg-clinic-tint/40 disabled:opacity-50">
                  <Icon className="h-5 w-5 shrink-0 text-clinic" aria-hidden />
                  <span><span className="block font-semibold leading-tight">{title}</span><span className="block text-[13px] text-ink-soft">{what}</span></span>
                </button>
              ))}
            </div>
          </section>
        )}

        <form className="rounded-2xl border border-line bg-white p-5" onSubmit={(e) => { e.preventDefault(); go(() => login(email, password)); }}>
          <label className="block text-[14px] font-medium" htmlFor="email">Email</label>
          <input id="email" type="email" autoComplete="username" required value={email} onChange={(e) => setEmail(e.target.value)}
            className="mt-1 h-11 w-full rounded-xl border border-line bg-page px-3 focus:border-clinic focus:bg-white focus-visible:outline-none" />
          <label className="mt-3 block text-[14px] font-medium" htmlFor="password">Password</label>
          <input id="password" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)}
            className="mt-1 h-11 w-full rounded-xl border border-line bg-page px-3 focus:border-clinic focus:bg-white focus-visible:outline-none" />
          {error && <p role="alert" className="mt-3 rounded-lg bg-alarm/10 px-3 py-2 text-[14px] text-alarm-deep">{error}</p>}
          <button type="submit" disabled={busy} className="mt-4 flex h-11 w-full items-center justify-center gap-2 rounded-xl bg-clinic font-semibold text-white hover:bg-clinic-deep disabled:opacity-50">
            <LogIn className="h-4 w-4" aria-hidden /> Sign in
          </button>
          <p className="mt-3 text-[12px] text-ink-soft">Accounts are created by the clinic owner. Five wrong passwords lock the account for 15 minutes.</p>
        </form>
      </div>
    </div>
  );
}
