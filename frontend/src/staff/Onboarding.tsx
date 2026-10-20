import { clsx } from "clsx";
import { Globe, Loader2 } from "lucide-react";
import { useEffect, useState } from "react";
import { staffGet, staffPost } from "./api";

interface Note { field: string; value: unknown; status: "found" | "check" }
interface DoctorDraft { name: string; specialty: string | null; days: string | null; hours: string | null; fee_inr: number | null }
interface Draft {
  id: string; source_value: string; sources: { url: string; title: string }[]; status: string; review: Note[];
  draft: { clinic_name: string | null; address: string | null; phone: string | null; booking_style: string | null; clinic_hours: string | null;
           doctors: DoctorDraft[]; services: { name: string; fee_inr: number | null }[] };
}

function Badge({ note }: { note?: Note }) {
  if (!note) return null;
  return <span className={clsx("ml-2 rounded-full px-2 py-0.5 text-[11px] font-semibold",
    note.status === "found" ? "bg-clinic-tint text-clinic-deep" : "bg-[#fbefd9] text-handoff")}>
    {note.status === "found" ? "on the page" : "check this"}</span>;
}

/** Owners: draft a clinic listing from its website with Tavily + Nemotron; every value is marked for review. */
export function Onboarding() {
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [draft, setDraft] = useState<Draft | null>(null);
  useEffect(() => { staffGet<{ drafts: Draft[] }>("/ops/onboarding/drafts").then((r) => setDraft(r.drafts[0] ?? null)).catch(() => {}); }, []);

  const make = async () => {
    const v = input.trim();
    if (!v) return;
    setBusy(true); setError(null);
    try { setDraft(await staffPost<Draft>("/ops/onboarding/draft", /^https?:\/\//.test(v) ? { url: v } : { name: v })); }
    catch (e) { setError(e instanceof Error ? e.message : "That didn't work."); }
    finally { setBusy(false); }
  };
  const decide = async (d: "approve" | "reject") => { if (draft) setDraft(await staffPost<Draft>(`/ops/onboarding/drafts/${draft.id}/${d}`)); };
  const note = (f: string) => draft?.review.find((n) => n.field === f);

  return (
    <section className="rounded-2xl border border-line bg-white p-4" aria-label="Clinic onboarding">
      <div className="flex items-baseline justify-between gap-3">
        <h2 className="font-semibold">Add a clinic from its website</h2>
        <p className="text-[12px] text-ink-soft">Tavily reads the pages · Nemotron drafts · you review</p>
      </div>
      <form className="mt-3 flex gap-2" onSubmit={(e) => { e.preventDefault(); void make(); }}>
        <label htmlFor="onb" className="sr-only">Clinic website or name</label>
        <input id="onb" value={input} onChange={(e) => setInput(e.target.value)} placeholder="https://clinic-website.in or “Clinic name, area”"
          className="h-10 flex-1 rounded-xl border border-line bg-page px-3 text-[15px] focus:border-clinic focus:bg-white focus-visible:outline-none" />
        <button type="submit" disabled={busy || !input.trim()} className="flex items-center gap-2 rounded-xl bg-clinic px-4 text-[14px] font-semibold text-white hover:bg-clinic-deep disabled:opacity-50">
          {busy ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden /> : <Globe className="h-4 w-4" aria-hidden />} {busy ? "Reading…" : "Draft it"}
        </button>
      </form>
      {error && <p role="alert" className="mt-2 rounded-lg bg-alarm/10 px-3 py-2 text-[14px] text-alarm-deep">{error}</p>}
      {draft && (
        <div className="mt-4 text-[14px]">
          <p className="text-[18px] font-semibold">{draft.draft.clinic_name ?? "Unnamed clinic"}
            <span className={clsx("ml-2 align-middle rounded-full px-2 py-0.5 text-[11px] font-semibold uppercase",
              draft.status === "approved" ? "bg-clinic text-white" : draft.status === "rejected" ? "bg-page text-ink-soft" : "bg-stamp/10 text-stamp")}>{draft.status}</span></p>
          {draft.draft.address && <p className="text-ink-soft">{draft.draft.address}<Badge note={note("address")} /></p>}
          {draft.draft.phone && <p className="text-ink-soft">{draft.draft.phone}<Badge note={note("phone")} /></p>}
          <table className="mt-3 w-full text-left">
            <thead className="text-[12px] text-ink-soft"><tr><th className="py-1 font-medium">Doctor</th><th className="font-medium">When</th><th className="font-medium">Fee</th></tr></thead>
            <tbody className="divide-y divide-line/70">
              {draft.draft.doctors.map((d, i) => (
                <tr key={i}>
                  <td className="py-1.5 pr-2"><span className="font-medium">{d.name}</span><Badge note={note(`doctors[${i}].name`)} />
                    {d.specialty && <span className="block text-[12px] text-ink-soft">{d.specialty}</span>}</td>
                  <td className="pr-2 text-[13px]">{[d.days, d.hours].filter(Boolean).join(" · ") || "—"}</td>
                  <td className="tabular">{d.fee_inr != null ? `₹${d.fee_inr}` : "—"}<Badge note={note(`doctors[${i}].fee_inr`)} /></td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="mt-3 text-[12px] text-ink-soft">Sources: {draft.sources.map((s, i) => (
            <a key={s.url} href={s.url} target="_blank" rel="noreferrer" className="text-clinic-deep underline">{i ? ", " : ""}{s.title || s.url}</a>))}</p>
          {draft.status === "draft" && (
            <div className="mt-3 flex gap-2">
              <button type="button" onClick={() => decide("approve")} className="rounded-lg bg-clinic px-3 py-1.5 text-[14px] font-semibold text-white hover:bg-clinic-deep">Approve draft</button>
              <button type="button" onClick={() => decide("reject")} className="rounded-lg px-3 py-1.5 text-[14px] text-ink-soft hover:bg-page">Reject</button>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
