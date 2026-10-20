import type { Lang } from "../patient/types";

/**
 * Speech in and out, behind one small interface so a server-side provider (an NVIDIA speech NIM, or an
 * Indian-language service) can replace the browser later without touching the call screen.
 *
 * Today: the browser's Web Speech API.
 * - Listening: SpeechRecognition (Chrome, Edge, Safari). In Chrome the audio is sent to Google's speech
 *   service to be turned into text; the call screen says so.
 * - Speaking: speechSynthesis with a voice for the language, if the device has one. Hindi voices are
 *   common; Kannada ones are not (Edge has one; Android has one if the Google TTS Kannada pack is installed).
 *   With no voice for the language, `speak` resolves false and the screen shows the words instead.
 */
export interface ListenHandlers {
  onPartial: (text: string) => void;
  onFinal: (text: string) => void;
  onError: (kind: ListenError) => void;
  onEnd: () => void;
}
export type ListenError = "no-speech" | "not-allowed" | "network" | "unsupported" | "other";

export interface SpeechEngine {
  canListen(): boolean;
  canSpeak(lang: Lang): boolean;
  listen(lang: Lang, h: ListenHandlers): () => void;     // returns stop()
  speak(text: string, lang: Lang): Promise<boolean>;      // false: nothing was spoken (no voice / unsupported)
  stopSpeaking(): void;
}

export const BCP47: Record<Lang, string> = { en: "en-IN", hi: "hi-IN", kn: "kn-IN" };

// --- minimal typings for the Web Speech API (not in TypeScript's DOM lib) ---
interface RecognitionResult { isFinal: boolean; 0: { transcript: string } }
interface RecognitionEvent { resultIndex: number; results: ArrayLike<RecognitionResult> }
interface Recognition {
  lang: string; interimResults: boolean; continuous: boolean; maxAlternatives: number;
  onresult: ((e: RecognitionEvent) => void) | null;
  onerror: ((e: { error: string }) => void) | null;
  onend: (() => void) | null;
  start(): void; stop(): void; abort(): void;
}
type RecognitionCtor = new () => Recognition;

function recognitionCtor(): RecognitionCtor | null {
  const w = globalThis as unknown as { SpeechRecognition?: RecognitionCtor; webkitSpeechRecognition?: RecognitionCtor };
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

/** The best installed voice for a language: exact locale, then same language; prefer natural/online voices. */
export function pickVoice(voices: SpeechSynthesisVoice[], lang: Lang): SpeechSynthesisVoice | null {
  const want = BCP47[lang].toLowerCase();
  const norm = (v: SpeechSynthesisVoice) => v.lang.replace("_", "-").toLowerCase();
  const same = voices.filter((v) => norm(v) === want || norm(v).startsWith(`${lang}-`) || norm(v) === lang);
  if (!same.length) return null;
  const score = (v: SpeechSynthesisVoice) =>
    (norm(v) === want ? 4 : 0) + (/natural|online|google|neural/i.test(v.name) ? 2 : 0) + (v.localService ? 0 : 1);
  return [...same].sort((a, b) => score(b) - score(a))[0];
}

const ERR: Record<string, ListenError> = {
  "no-speech": "no-speech", "not-allowed": "not-allowed", "service-not-allowed": "not-allowed",
  "audio-capture": "not-allowed", network: "network", "language-not-supported": "unsupported",
};

export function browserEngine(): SpeechEngine {
  const synth: SpeechSynthesis | undefined = (globalThis as { speechSynthesis?: SpeechSynthesis }).speechSynthesis;
  let voices: SpeechSynthesisVoice[] = synth?.getVoices() ?? [];
  synth?.addEventListener?.("voiceschanged", () => { voices = synth.getVoices(); });
  let current: SpeechSynthesisUtterance | null = null;     // keep a reference: Chrome drops onend for collected utterances

  return {
    canListen: () => recognitionCtor() !== null,
    canSpeak: (lang) => !!synth && pickVoice(voices.length ? voices : synth.getVoices(), lang) !== null,

    listen(lang, h) {
      const Ctor = recognitionCtor();
      if (!Ctor) {
        h.onError("unsupported");
        h.onEnd();
        return () => {};
      }
      const rec = new Ctor();
      rec.lang = BCP47[lang];
      rec.interimResults = true;
      rec.continuous = false;              // one utterance per turn: the clinic answers, then listens again
      rec.maxAlternatives = 1;
      let finalText = "";
      rec.onresult = (e) => {
        let partial = "";
        for (let i = e.resultIndex; i < e.results.length; i++) {
          const r = e.results[i];
          if (r.isFinal) finalText += r[0].transcript;
          else partial += r[0].transcript;
        }
        if (partial) h.onPartial((finalText + " " + partial).trim());
      };
      rec.onerror = (e) => h.onError(ERR[e.error] ?? "other");
      rec.onend = () => {
        if (finalText.trim()) h.onFinal(finalText.trim());
        h.onEnd();
      };
      try {
        rec.start();
      } catch {
        h.onError("other");
        h.onEnd();
      }
      return () => { try { rec.stop(); } catch { /* already stopped */ } };
    },

    speak(text, lang) {
      return new Promise((resolve) => {
        if (!synth || !text.trim()) return resolve(false);
        const voice = pickVoice(voices.length ? voices : synth.getVoices(), lang);
        if (!voice) return resolve(false);
        synth.cancel();
        const u = new SpeechSynthesisUtterance(text);
        u.voice = voice;
        u.lang = voice.lang;
        u.rate = lang === "en" ? 1.0 : 0.95;
        current = u;
        const done = (ok: boolean) => { if (current === u) current = null; resolve(ok); };
        u.onend = () => done(true);
        u.onerror = () => done(false);
        synth.speak(u);
      });
    },

    stopSpeaking() { current = null; synth?.cancel(); },
  };
}
