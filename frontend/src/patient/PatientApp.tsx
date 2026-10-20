import { clsx } from "clsx";
import { Phone, RotateCcw, SendHorizontal } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import { BookedSlip, ConfirmSlip, ConsentCard, EmergencyScreen, TimeOptions } from "./Cards";
import { ClinicPanel, useClinic } from "./ClinicPanel";
import { tr } from "./i18n";
import type { Lang, Message } from "./types";
import { useConversation } from "./useConversation";
import { CallScreen } from "../voice/CallScreen";

const LANGS: { code: Lang; label: string; name: string }[] = [
  { code: "en", label: "EN", name: "English" },
  { code: "hi", label: "हिं", name: "हिन्दी" },
  { code: "kn", label: "ಕ", name: "ಕನ್ನಡ" },
];

function LanguagePicker({ value, onChange }: { value: Lang; onChange: (l: Lang) => void }) {
  return (
    <div role="radiogroup" aria-label="Language" className="flex rounded-full bg-page p-0.5">
      {LANGS.map((l) => (
        <button key={l.code} type="button" role="radio" aria-checked={value === l.code} aria-label={l.name}
          onClick={() => onChange(l.code)}
          className={clsx("h-8 min-w-8 rounded-full px-2 text-[15px] font-semibold transition-colors",
            value === l.code ? "bg-clinic text-white" : "text-ink-soft hover:text-ink")}>
          {l.label}
        </button>
      ))}
    </div>
  );
}

function Bubble({ m }: { m: Message }) {
  if (!m.text) return null;
  if (m.role === "user")
    return <p className="ml-auto max-w-[82%] whitespace-pre-line rounded-2xl rounded-br-md bg-clinic-tint px-4 py-2.5 text-[16px] leading-snug">{m.text}</p>;
  return <p className="max-w-[92%] whitespace-pre-line text-[16px] leading-relaxed">{m.text}</p>;
}

export default function PatientApp() {
  const conv = useConversation("en");
  const { state, say, answer, setLanguage, reset } = conv;
  const [calling, setCalling] = useState(false);
  const clinic = useClinic();
  const [draft, setDraft] = useState("");
  const [dismissed, setDismissed] = useState<string | null>(null);
  const scroller = useRef<HTMLDivElement>(null);
  const lang = state.language;
  const last = state.messages[state.messages.length - 1];
  const lastAssistant = [...state.messages].reverse().find((m) => m.role === "assistant");
  const emergency = lastAssistant?.ui?.emergency && dismissed !== lastAssistant.id ? lastAssistant : null;

  useEffect(() => {
    scroller.current?.scrollTo({ top: scroller.current.scrollHeight, behavior: "smooth" });
  }, [state.messages.length, state.card, state.status]);

  useEffect(() => { document.documentElement.lang = lang; }, [lang]);

  // Judge scenarios (landing page): /app?lang=hi&say=… starts that conversation; ?call=1 opens the call screen.
  const started = useRef(false);
  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const q = new URLSearchParams(window.location.search);
    const l = q.get("lang");
    if (l === "en" || l === "hi" || l === "kn") setLanguage(l);
    const text = q.get("say");
    if (text) setTimeout(() => say(text.slice(0, 500)), 300);
    if (q.get("call") === "1") setCalling(true);
  }, [say, setLanguage]);

  const suggestions = useMemo(() => [tr("suggestBook", lang), tr("suggestHours", lang), tr("suggestSkin", lang)], [lang]);
  const send = (text: string) => { say(text); setDraft(""); };

  return (
    <div className="flex min-h-full justify-center gap-12 lg:px-10">
      <aside className="hidden w-72 shrink-0 pt-12 lg:block"><ClinicPanel info={clinic} lang={lang} /></aside>

      <main className="flex h-dvh w-full max-w-[480px] flex-col bg-white lg:my-6 lg:h-[calc(100dvh-3rem)] lg:rounded-3xl lg:border lg:border-line">
        <header className="flex items-center gap-3 border-b border-line px-4 py-3">
          <div aria-hidden className="grid h-9 w-9 place-items-center rounded-xl bg-clinic">
            <svg viewBox="0 0 32 32" className="h-5 w-5"><path d="M13 8h6v5h5v6h-5v5h-6v-5H8v-6h5z" fill="#F2F6F5" /></svg>
          </div>
          <div className="min-w-0 flex-1">
            <p className="text-[15px] font-semibold leading-tight">{clinic?.name ?? "Sunrise Family Clinic"}</p>
            <p className="text-[13px] leading-tight text-ink-soft">{tr("title", lang)}</p>
          </div>
          <LanguagePicker value={lang} onChange={setLanguage} />
          <button type="button" onClick={() => setCalling(true)} aria-label={tr("call", lang)} title={tr("call", lang)}
            className="grid h-9 w-9 place-items-center rounded-full bg-clinic-tint text-clinic-deep hover:bg-clinic hover:text-white">
            <Phone className="h-[18px] w-[18px]" aria-hidden />
          </button>
          {state.messages.length > 0 && (
            <button type="button" onClick={reset} aria-label={tr("newChat", lang)} title={tr("newChat", lang)}
              className="grid h-9 w-9 place-items-center rounded-full text-ink-soft hover:bg-page hover:text-ink">
              <RotateCcw className="h-[18px] w-[18px]" aria-hidden />
            </button>
          )}
        </header>

        <div ref={scroller} className="flex-1 overflow-y-auto px-4 py-5" aria-live="polite">
          <div className="flex flex-col gap-4">
            <p className="max-w-[92%] text-[16px] leading-relaxed">{tr("welcome", lang)}</p>
            {state.messages.length === 0 && (
              <button type="button" onClick={() => setCalling(true)}
                className="flex items-center gap-3 self-start rounded-2xl bg-clinic px-5 py-3.5 text-left text-white shadow-[0_8px_24px_-12px_rgba(15,110,99,.8)] hover:bg-clinic-deep">
                <span className="grid h-10 w-10 place-items-center rounded-full bg-white/15"><Phone className="h-5 w-5" aria-hidden /></span>
                <span>
                  <span className="block text-[17px] font-semibold leading-tight">{tr("call", lang)}</span>
                  <span className="block text-[14px] leading-tight text-white/80">English · हिन्दी · ಕನ್ನಡ</span>
                </span>
              </button>
            )}
            {state.messages.length === 0 && (
              <div className="flex flex-wrap gap-2">
                {suggestions.map((s) => (
                  <button key={s} type="button" onClick={() => send(s)}
                    className="rounded-full border border-line px-3.5 py-1.5 text-[15px] text-ink hover:border-clinic hover:text-clinic-deep">
                    {s}
                  </button>
                ))}
              </div>
            )}
            {state.messages.filter((m) => m.text || m.ui?.booked || m.ui?.handoff || (m === last && m.ui?.options)).map((m) => (
              <div key={m.id}>
                <Bubble m={m} />
                {m.ui?.booked && <BookedSlip booked={m.ui.booked} lang={lang} />}
                {m.ui?.handoff && <p className="mt-2 border-l-4 border-handoff pl-3 text-[15px]">{tr("handoff", lang)}</p>}
                {m === last && m.ui?.options && !state.card && (
                  <TimeOptions title={m.ui.options_title} options={m.ui.options} disabled={state.busy}
                    onPick={(o) => say(String(o.n), o.when)} />
                )}
              </div>
            ))}
            {state.card?.kind === "consent" && <ConsentCard card={state.card} lang={lang} onAnswer={answer} disabled={state.busy} />}
            {state.card?.kind === "confirm" && <ConfirmSlip card={state.card} lang={lang} onAnswer={answer} disabled={state.busy} />}
            {state.error && (
              <p role="alert" className="rounded-xl bg-alarm/8 px-4 py-3 text-[15px] text-alarm-deep">
                {tr(state.error === "busy" ? "errorBusy" : "errorNetwork", lang)}
              </p>
            )}
          </div>
        </div>

        <footer className="border-t border-line px-3 pb-[max(0.75rem,env(safe-area-inset-bottom))] pt-2">
          <p className="flex h-6 items-center gap-2 px-1 text-[14px] text-ink-soft" aria-live="polite">
            {state.busy && <span aria-hidden className="pulse-dot h-2 w-2 rounded-full bg-clinic" />}
            {state.busy ? state.status ?? "" : ""}
          </p>
          <form className="flex items-end gap-2" onSubmit={(e) => { e.preventDefault(); send(draft); }}>
            <label htmlFor="msg" className="sr-only">{tr("placeholder", lang)}</label>
            <input id="msg" value={draft} onChange={(e) => setDraft(e.target.value)} autoComplete="off" enterKeyHint="send"
              placeholder={state.card ? tr("placeholderCard", lang) : tr("placeholder", lang)} maxLength={1000}
              className="h-12 flex-1 rounded-2xl border border-line bg-page px-4 text-[16px] placeholder:text-ink-soft/70 focus:border-clinic focus:bg-white focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-clinic/30" />
            <button type="submit" disabled={!draft.trim() || state.busy} aria-label={tr("send", lang)}
              className="grid h-12 w-12 place-items-center rounded-2xl bg-clinic text-white hover:bg-clinic-deep disabled:bg-line">
              <SendHorizontal className="h-5 w-5" aria-hidden />
            </button>
          </form>
        </footer>
      </main>

      {calling && <CallScreen conv={conv} clinicName={clinic?.name ?? "Sunrise Family Clinic"} onClose={() => setCalling(false)} />}

      {emergency?.ui?.emergency && (
        <EmergencyScreen emergency={emergency.ui.emergency} text={emergency.text} lang={lang}
          onNotUrgent={() => { setDismissed(emergency.id); say("not urgent", tr("notUrgent", lang)); }}
          onClose={() => setDismissed(emergency.id)} />
      )}
    </div>
  );
}
