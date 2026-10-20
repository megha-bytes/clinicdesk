import { clsx } from "clsx";
import { Phone } from "lucide-react";
import { tr } from "./i18n";
import type { Booked, Card, Emergency, Lang, SlotOption } from "./types";

/** Splits "Wed 21 Oct, 9:50 AM" into the day and the time. Token labels are shown whole. */
function splitWhen(when: string): { day: string; time: string } {
  const i = when.lastIndexOf(", ");
  if (i < 0 || when.startsWith("token")) return { day: "", time: when };
  return { day: when.slice(0, i), time: when.slice(i + 2) };
}

export function TimeOptions({ title, options, onPick, disabled }: {
  title?: string; options: SlotOption[]; onPick: (o: SlotOption) => void; disabled?: boolean;
}) {
  const showDoctor = new Set(options.map((o) => o.doctor)).size > 1;
  return (
    <div className="mt-3" role="group" aria-label={title}>
      {title && <p className="text-[15px] text-ink-soft mb-2">{title}</p>}
      <div className="flex flex-wrap gap-2">
        {options.map((o) => {
          const { day, time } = splitWhen(o.when);
          return (
            <button
              key={o.n}
              type="button"
              disabled={disabled}
              onClick={() => onPick(o)}
              className="group min-w-[104px] rounded-xl border-2 border-clinic/25 bg-white px-4 py-2.5 text-left transition-colors hover:border-clinic hover:bg-clinic-tint/50 active:bg-clinic-tint disabled:opacity-50"
            >
              <span className="block text-xl font-semibold leading-tight text-clinic-deep tabular">{time}</span>
              {day && <span className="block text-[13px] text-ink-soft leading-tight">{day}</span>}
              {showDoctor && o.doctor && <span className="block text-[13px] text-ink leading-tight mt-0.5">{o.doctor}</span>}
            </button>
          );
        })}
      </div>
    </div>
  );
}

export function ConsentCard({ card, lang, onAnswer, disabled }: {
  card: Card; lang: Lang; onAnswer: (yes: boolean, label: string) => void; disabled?: boolean;
}) {
  return (
    <section className="mt-3 rounded-2xl border border-line bg-white p-4" aria-live="polite">
      <h3 className="font-semibold text-[17px]">{tr("consentTitle", lang)}</h3>
      <p className="mt-1.5 text-[15px] leading-relaxed text-ink">{card.summary}</p>
      <div className="mt-4 flex gap-2">
        <button type="button" disabled={disabled} onClick={() => onAnswer(true, tr("agree", lang))}
          className="rounded-xl bg-clinic px-5 py-2.5 font-semibold text-white hover:bg-clinic-deep disabled:opacity-50">
          {tr("agree", lang)}
        </button>
        <button type="button" disabled={disabled} onClick={() => onAnswer(false, tr("disagree", lang))}
          className="rounded-xl px-4 py-2.5 font-medium text-ink-soft hover:bg-page disabled:opacity-50">
          {tr("disagree", lang)}
        </button>
      </div>
    </section>
  );
}

function SlipRows({ rows }: { rows: [string, string | number | null | undefined][] }) {
  return (
    <dl className="mt-3 grid grid-cols-[auto_1fr] gap-x-6 gap-y-1 text-[15px]">
      {rows.filter(([, v]) => v !== undefined && v !== null && v !== "").map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="text-ink-soft">{k}</dt>
          <dd className="font-medium tabular">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function ConfirmSlip({ card, lang, onAnswer, disabled }: {
  card: Card; lang: Lang; onAnswer: (yes: boolean, label: string) => void; disabled?: boolean;
}) {
  const d = card.details ?? {};
  const action = d.action ?? card.action;
  const title = action === "cancel" ? tr("cancelTitle", lang) : action === "reschedule" ? tr("moveTitle", lang) : tr("confirmTitle", lang);
  const yes = action === "cancel" ? tr("yesCancel", lang) : action === "reschedule" ? tr("yesMove", lang) : tr("yesBook", lang);
  const when = splitWhen(action === "reschedule" ? d.new_when ?? "" : d.when ?? "");
  return (
    <section className="slip slip-draft mt-3 rounded-b-2xl border-x-2 border-b-2 border-dashed border-clinic/35 px-5 pb-5" aria-live="polite">
      <h3 className="font-semibold text-[17px]">{title}</h3>
      {d.doctor ? (
        <>
          <p className="mt-2 text-3xl font-bold leading-none text-clinic-deep tabular">{when.time}</p>
          {when.day && <p className="text-[15px] text-ink-soft mt-1">{when.day}</p>}
          <SlipRows rows={[["Doctor", d.doctor], ["Visit", d.type], [tr("fee", lang), d.fee_inr ? `₹${d.fee_inr}` : null]]} />
        </>
      ) : (
        <p className="mt-2 text-[15px]">{card.summary}</p>
      )}
      <p className="sr-only">{card.summary}</p>
      <div className="mt-4 flex gap-2">
        <button type="button" disabled={disabled} onClick={() => onAnswer(true, yes)}
          className="rounded-xl bg-clinic px-5 py-2.5 font-semibold text-white hover:bg-clinic-deep disabled:opacity-50">{yes}</button>
        <button type="button" disabled={disabled} onClick={() => onAnswer(false, tr("no", lang))}
          className="rounded-xl px-4 py-2.5 font-medium text-ink-soft hover:bg-page disabled:opacity-50">{tr("no", lang)}</button>
      </div>
    </section>
  );
}

export function BookedSlip({ booked, lang }: { booked: Booked; lang: Lang }) {
  const when = splitWhen(booked.when);
  return (
    <section className="slip print-in mt-3 rounded-b-2xl px-5 pb-5 shadow-[0_1px_0_#cfdcd9,0_8px_24px_-12px_rgba(15,110,99,.35)]" aria-label={tr("booked", lang)}>
      <div className="flex items-start justify-between gap-4">
        <div>
          <p className="text-[15px] text-ink-soft">{booked.doctor}</p>
          <p className="mt-1 text-4xl font-bold leading-none text-ink tabular">{when.time}</p>
          {when.day && <p className="text-[15px] text-ink-soft mt-1">{when.day}</p>}
        </div>
        <div className="stamp stamp-in grid h-20 w-20 shrink-0 place-items-center text-center text-lg font-bold leading-none">
          {tr("booked", lang)}
        </div>
      </div>
      <SlipRows rows={[["Visit", booked.type === "new" ? "New consultation" : booked.type === "follow_up" ? "Follow-up" : booked.type],
                       [tr("token", lang), booked.token_no], [tr("fee", lang), `₹${booked.fee_inr}`]]} />
      {booked.pay_url && (
        <a href={booked.pay_url} target="_blank" rel="noreferrer"
           className="mt-4 inline-block rounded-xl bg-clinic px-5 py-2.5 font-semibold text-white hover:bg-clinic-deep">
          {tr("pay", lang, { fee: booked.fee_inr })}
        </a>
      )}
      <p className="mt-2 text-[13px] text-ink-soft">{tr("payLater", lang)}</p>
    </section>
  );
}

export function EmergencyScreen({ emergency, text, lang, onNotUrgent, onClose }: {
  emergency: Emergency; text: string; lang: Lang; onNotUrgent: () => void; onClose: () => void;
}) {
  const n = emergency.numbers;
  const selfHarm = emergency.kind === "self_harm";
  const title = selfHarm ? tr("selfHarmTitle", lang) : tr("emergencyTitle", lang);
  // The message starts with the same sentence as the title; show the rest under it.
  const body = text.startsWith(title) ? text.slice(title.length).replace(/^[.!।]\s*/, "") : text;
  const calls = selfHarm
    ? [[n.mental_health, tr("callMental", lang, { n: n.mental_health })], [n.emergency, tr("callEmergency", lang, { n: n.emergency })]]
    : [[n.ambulance, tr("callAmbulance", lang, { n: n.ambulance })], [n.emergency, tr("callEmergency", lang, { n: n.emergency })]];
  return (
    <div role="alertdialog" aria-modal="true" aria-labelledby="em-title"
         className="fixed inset-0 z-50 flex flex-col bg-alarm px-6 pb-8 pt-[max(2.5rem,env(safe-area-inset-top))] text-white">
      <div className="mx-auto flex w-full max-w-md flex-1 flex-col">
        <h2 id="em-title" className="text-3xl font-bold leading-tight">{title}</h2>
        <p className="mt-3 text-lg leading-relaxed text-white/90">{body}</p>
        <div className="mt-8 flex flex-col gap-3">
          {calls.map(([num, label]) => (
            <a key={num} href={`tel:${num}`}
               className="flex items-center gap-3 rounded-2xl bg-white px-5 py-4 text-lg font-bold text-alarm-deep shadow-lg">
              <Phone aria-hidden className="h-6 w-6" /> {label}
            </a>
          ))}
        </div>
        <p className="mt-6 text-white/85">{tr("staffAlerted", lang)}</p>
        <div className="mt-auto flex flex-wrap gap-3 pt-8">
          {!selfHarm && (
            <button type="button" onClick={onNotUrgent} className="rounded-xl border-2 border-white/70 px-4 py-2.5 font-semibold">
              {tr("notUrgent", lang)}
            </button>
          )}
          <button type="button" onClick={onClose} className={clsx("rounded-xl px-4 py-2.5 font-medium text-white/85 underline-offset-4 hover:underline")}>
            {tr("backToChat", lang)}
          </button>
        </div>
      </div>
    </div>
  );
}
