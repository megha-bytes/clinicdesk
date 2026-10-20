import { clsx } from "clsx";
import { Mic, MicOff, PhoneOff, Volume2, VolumeX } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { BookedSlip, ConfirmSlip, ConsentCard, TimeOptions } from "../patient/Cards";
import { tr } from "../patient/i18n";
import type { Lang } from "../patient/types";
import type { useConversation } from "../patient/useConversation";
import { browserEngine, type SpeechEngine } from "./engine";
import { useCall, type Phase } from "./useCall";

type Conversation = ReturnType<typeof useConversation>;

function useClock(since: number, running: boolean) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!running) return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [running]);
  const s = Math.max(0, Math.floor((now - since) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const PHASE_LABEL: Record<Phase, Parameters<typeof tr>[0]> = {
  connecting: "connecting", speaking: "speaking", listening: "listening", thinking: "thinking",
  paused: "tapToTalk", ended: "callEnded",
};

/** The rings around the mic: they breathe while listening and ripple out while the clinic speaks. */
function MicButton({ phase, onTap, lang, compact }: { phase: Phase; onTap: () => void; lang: Lang; compact?: boolean }) {
  const label = phase === "speaking" ? tr("tapToInterrupt", lang) : phase === "listening" ? tr("tapWhenDone", lang)
    : tr("tapToTalk", lang);
  return (
    <div className={clsx("relative grid place-items-center", compact ? "h-28 w-28" : "h-44 w-44")}>
      {phase === "listening" && <>
        <span aria-hidden className="call-ring absolute inset-0 rounded-full bg-white/15" />
        <span aria-hidden className="call-ring call-ring-2 absolute inset-0 rounded-full bg-white/10" />
      </>}
      {phase === "speaking" && <span aria-hidden className="call-ripple absolute inset-3 rounded-full border-2 border-white/40" />}
      <button type="button" onClick={onTap} disabled={phase === "thinking" || phase === "connecting"} aria-label={label}
        className={clsx("relative grid place-items-center rounded-full", compact ? "h-20 w-20" : "h-28 w-28",
          " shadow-[0_12px_40px_-8px_rgba(0,0,0,.45)] transition-all",
          phase === "listening" ? "scale-105 bg-white text-clinic-deep" : "bg-white/95 text-clinic-deep hover:bg-white",
          (phase === "thinking" || phase === "connecting") && "opacity-70")}>
        {phase === "thinking"
          ? <span aria-hidden className="flex gap-1.5">{[0, 1, 2].map((i) => <span key={i} className="think-dot h-2.5 w-2.5 rounded-full bg-clinic" style={{ animationDelay: `${i * 160}ms` }} />)}</span>
          : <Mic className={compact ? "h-8 w-8" : "h-11 w-11"} aria-hidden strokeWidth={2.2} />}
      </button>
    </div>
  );
}

export function CallScreen({ conv, clinicName, onClose, engine }: {
  conv: Conversation; clinicName: string; onClose: () => void; engine?: SpeechEngine;
}) {
  const speech = useMemo(() => engine ?? browserEngine(), [engine]);
  const call = useCall(conv, speech, clinicName);
  const { state } = conv;
  const lang = state.language;
  const clock = useClock(call.startedAt, call.phase !== "ended");
  const last = state.messages[state.messages.length - 1];
  const lastAssistant = last?.role === "assistant" ? last : null;
  const showSheet = !!(state.card || lastAssistant?.ui?.options || lastAssistant?.ui?.booked);
  // the time buttons already show the options, so the caption doesn't repeat them
  const caption = lastAssistant?.ui?.options && !state.card ? (lastAssistant.text || "") : call.caption;

  useEffect(() => { if (call.phase === "ended") { const t = setTimeout(onClose, 700); return () => clearTimeout(t); } }, [call.phase, onClose]);

  const problem = call.problem === "not-allowed" ? tr("micBlocked", lang)
    : call.problem === "unsupported" || !call.canListen ? tr("noListen", lang)
    : call.problem === "network" ? tr("errorNetwork", lang) : null;
  const status = call.phase === "thinking" && state.status ? state.status : tr(PHASE_LABEL[call.phase], lang);

  return (
    <div role="dialog" aria-modal="true" aria-label={tr("call", lang)}
      className="call-bg fixed inset-0 z-40 flex flex-col text-white">
      <div className="mx-auto flex min-h-0 w-full max-w-[480px] flex-1 flex-col px-5 pb-[max(1.25rem,env(safe-area-inset-bottom))] pt-[max(1.25rem,env(safe-area-inset-top))]">
        <header className="flex items-start justify-between gap-3">
          <div>
            <p className="text-[13px] uppercase tracking-[0.14em] text-white/60">{tr("title", lang)}</p>
            <p className="text-xl font-semibold leading-tight">{clinicName}</p>
            <p className="mt-0.5 text-[14px] tabular text-white/70" aria-label="Call time">{clock}</p>
          </div>
          <button type="button" onClick={call.toggleMute} aria-pressed={!call.muted} aria-label={tr("speakerOn", lang)}
            title={tr("speakerOn", lang)} className="grid h-11 w-11 place-items-center rounded-full bg-white/10 hover:bg-white/20">
            {call.muted ? <VolumeX className="h-5 w-5" aria-hidden /> : <Volume2 className="h-5 w-5" aria-hidden />}
          </button>
        </header>

        {/* what was said, as live captions */}
        <section className="mt-6 flex min-h-0 flex-1 flex-col justify-end gap-4 overflow-y-auto pb-2" aria-live="polite">
          {call.heard && (
            <p className="self-end max-w-[85%] rounded-2xl rounded-br-md bg-white/12 px-4 py-2.5 text-[16px] leading-snug text-white/90">
              <span className="sr-only">{tr("you", lang)}: </span>{call.heard}
            </p>
          )}
          {caption && (
            <p className={clsx("font-medium leading-snug text-white", showSheet ? "text-[19px]" : "text-[22px]")} lang={lang}>{caption}</p>
          )}
          {!call.canSpeak && !call.muted && <p className="text-[14px] text-white/70">{tr("noVoice", lang)}</p>}

          {/* cards and options can still be tapped during a call */}
          {showSheet && (
            <div className="rounded-2xl bg-white p-1 text-ink shadow-xl [&>section]:mt-0">
              {state.card?.kind === "consent" && <ConsentCard card={state.card} lang={lang} onAnswer={call.tapAnswer} disabled={state.busy} />}
              {state.card?.kind === "confirm" && <ConfirmSlip card={state.card} lang={lang} onAnswer={call.tapAnswer} disabled={state.busy} />}
              {!state.card && lastAssistant?.ui?.options && (
                <div className="px-3 pb-3"><TimeOptions title={lastAssistant.ui.options_title} options={lastAssistant.ui.options}
                  disabled={state.busy} onPick={(o) => call.tapOption(o.n, o.when)} /></div>
              )}
              {!state.card && lastAssistant?.ui?.booked && <BookedSlip booked={lastAssistant.ui.booked} lang={lang} />}
            </div>
          )}
        </section>

        <p className="min-h-[1.75rem] text-center text-[17px] text-white/90" aria-live="polite">
          {call.phase === "listening" && call.partial ? <span className="italic">“{call.partial}”</span> : status}
        </p>
        {problem && <p role="alert" className="mt-1 rounded-xl bg-black/20 px-4 py-2.5 text-center text-[15px]">{problem}</p>}

        <div className="mt-3 flex flex-col items-center">
          <MicButton phase={call.phase} onTap={call.tapMic} lang={lang} compact={showSheet} />
          {state.messages.length === 0 && call.phase === "listening" && !call.partial && (
            <p className="-mt-2 text-center text-[14px] text-white/65">{tr("trySaying", lang)}</p>
          )}
        </div>

        <div className={clsx("flex items-center justify-center gap-6", showSheet ? "mt-2" : "mt-5")}>
          <button type="button" onClick={call.end} aria-label={tr("endCall", lang)}
            className="flex items-center gap-2 rounded-full bg-alarm px-6 py-3.5 text-[16px] font-semibold shadow-lg hover:bg-alarm-deep">
            <PhoneOff className="h-5 w-5" aria-hidden /> {tr("endCall", lang)}
          </button>
          {!call.canListen && <MicOff className="h-6 w-6 text-white/60" aria-hidden />}
        </div>
        <p className="mt-4 text-center text-[12px] leading-snug text-white/55">{tr("speechNote", lang)}</p>
      </div>
    </div>
  );
}
