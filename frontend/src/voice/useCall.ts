import { useCallback, useEffect, useRef, useState } from "react";
import { tr } from "../patient/i18n";
import type { Lang } from "../patient/types";
import type { useConversation } from "../patient/useConversation";
import type { ListenError, SpeechEngine } from "./engine";

/**
 * Turn-taking for a voice call, on top of the shared conversation engine:
 *
 *   greet → listen → (patient speaks) → send as channel "voice" → clinic thinks → speak the reply → listen …
 *
 * - The clinic speaks `state.spoken` (the backend's reply written for the ear), then listens again.
 * - Silence twice in a row, an emergency, or a blocked mic → stop and wait for a tap on the mic.
 * - A tap while the clinic is speaking interrupts it and listens (barge-in).
 * - With no voice for the language on this device, the reply is shown in large text and the call carries on.
 */
export type Phase = "connecting" | "speaking" | "listening" | "thinking" | "paused" | "ended";
type Conversation = ReturnType<typeof useConversation>;

export function useCall(conv: Conversation, engine: SpeechEngine, clinicName: string) {
  const { state, say, answer } = conv;
  const lang: Lang = state.language;
  const [phase, setPhase] = useState<Phase>("connecting");
  const [partial, setPartial] = useState("");
  const [heard, setHeard] = useState("");                 // the caller's last finished utterance
  const [caption, setCaption] = useState("");             // what the clinic last said
  const [problem, setProblem] = useState<ListenError | null>(null);
  const [muted, setMuted] = useState(false);
  const [startedAt] = useState(() => Date.now());

  const live = useRef(true);
  const phaseRef = useRef(phase);
  phaseRef.current = phase;
  const langRef = useRef(lang);
  langRef.current = lang;
  const mutedRef = useRef(muted);
  mutedRef.current = muted;
  const stopListening = useRef<() => void>(() => {});
  const misses = useRef(0);
  const handled = useRef(state.spoken?.n ?? 0);           // replies from before the call are not read out
  const lastError = useRef(state.error);

  const set = (p: Phase) => {
    if (!live.current) return;
    phaseRef.current = p;                           // synchronously, so a second listen() can see it
    setPhase(p);
  };

  const speak = useCallback(async (text: string) => {
    if (!live.current) return;
    setCaption(text);
    if (mutedRef.current || !engine.canSpeak(langRef.current)) return;
    set("speaking");
    await engine.speak(text, langRef.current);
  }, [engine]);

  const listen = useCallback(() => {
    if (!live.current || phaseRef.current === "listening") return;      // e.g. a tap interrupted the reply
    if (!engine.canListen()) { setProblem("unsupported"); set("paused"); return; }
    engine.stopSpeaking();
    setPartial("");
    setProblem(null);
    set("listening");
    let final = "";
    let error: ListenError | null = null;
    stopListening.current = engine.listen(langRef.current, {
      onPartial: (t) => live.current && setPartial(t),
      onFinal: (t) => { final = t; },
      onError: (k) => { error = k; },
      onEnd: () => {
        if (!live.current || phaseRef.current !== "listening") return;
        setPartial("");
        set("paused");                                // provisional; the branches below move on
        if (final) {
          misses.current = 0;
          setHeard(final);
          set("thinking");
          say(final, undefined, "voice");
          return;
        }
        if (error === "not-allowed" || error === "unsupported" || error === "network") {
          setProblem(error);
          set("paused");
          return;
        }
        misses.current += 1;                       // silence (or nothing usable)
        if (misses.current >= 2) { set("paused"); return; }
        void speak(tr("didntCatch", langRef.current)).then(listen);
      },
    });
  }, [say, engine, speak]);

  // Open the call: greet, then listen. The screen opens from a tap, so mobile browsers allow audio.
  // (React StrictMode mounts twice in development: the first greeting is cancelled cleanly.)
  useEffect(() => {
    live.current = true;
    let cancelled = false;
    void speak(tr("greeting", langRef.current, { clinic: clinicName })).then(() => { if (!cancelled) listen(); });
    return () => {
      cancelled = true;
      live.current = false;
      stopListening.current();
      engine.stopSpeaking();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // A new reply from the clinic: read it out, then listen (unless it's an emergency).
  useEffect(() => {
    const s = state.spoken;
    if (!s || s.n <= handled.current) return;
    handled.current = s.n;
    const last = state.messages[state.messages.length - 1];
    const emergency = last?.role === "assistant" && !!last.ui?.emergency;
    void speak(s.text).then(() => {
      if (!live.current) return;
      if (emergency) set("paused");
      else listen();
    });
  }, [state.spoken, state.messages, speak, listen]);

  // The request failed: say so and listen again.
  useEffect(() => {
    if (state.error && state.error !== lastError.current && phaseRef.current === "thinking") {
      void speak(tr(state.error === "busy" ? "errorBusy" : "callError", langRef.current)).then(() => set("paused"));
    }
    lastError.current = state.error;
  }, [state.error, speak]);

  /** The big mic button: interrupt the clinic, stop listening, or start listening. */
  const tapMic = useCallback(() => {
    if (phaseRef.current === "listening") { stopListening.current(); return; }
    if (phaseRef.current === "thinking") return;
    misses.current = 0;
    listen();
  }, [listen]);

  /** A tap on a card button or an option inside the call: same as saying it. */
  const tapAnswer = useCallback((approved: boolean, label: string) => {
    stopListening.current();
    engine.stopSpeaking();
    set("thinking");
    answer(approved, label, "voice");
  }, [answer, engine]);
  const tapOption = useCallback((n: number, when: string) => {
    stopListening.current();
    engine.stopSpeaking();
    set("thinking");
    say(String(n), when, "voice");
  }, [say, engine]);

  const end = useCallback(() => {
    live.current = false;
    stopListening.current();
    engine.stopSpeaking();
    setPhase("ended");
  }, [engine]);

  const toggleMute = useCallback(() => {
    setMuted((m) => { if (!m) engine.stopSpeaking(); return !m; });
  }, [engine]);

  return { phase, partial, heard, caption, problem, muted, startedAt, tapMic, tapAnswer, tapOption, toggleMute, end,
           canSpeak: engine.canSpeak(lang), canListen: engine.canListen() };
}
