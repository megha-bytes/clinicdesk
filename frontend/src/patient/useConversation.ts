import { useCallback, useEffect, useReducer, useRef } from "react";
import { API_URL, getJson } from "../realtime/api";
import { postStream, StreamError } from "../realtime/sse";
import type { Card, Channel, Lang, Message, RunResult } from "./types";

/**
 * The conversation engine shared by every way of talking to the clinic: the chat screen and the
 * call screen (voice). It streams AG-UI events from the backend and keeps: messages, the live status
 * line, the card waiting for an answer, errors, and `spoken` — what a voice call should read out after
 * each run (`n` changes on every run, so the call screen knows there is something new to say).
 */
export interface State {
  threadId: string | null;
  language: Lang;
  messages: Message[];
  status: string | null;
  busy: boolean;
  card: Card | null;
  error: "network" | "busy" | null;
  spoken: { n: number; text: string } | null;
}

type Action =
  | { type: "restore"; threadId: string; messages: Message[]; card: Card | null; language: Lang }
  | { type: "language"; language: Lang }
  | { type: "user"; text: string; display?: string }
  | { type: "started"; threadId: string }
  | { type: "status"; text: string }
  | { type: "card"; card: Card }
  | { type: "finished"; result: RunResult }
  | { type: "error"; error: "network" | "busy" }
  | { type: "reset" };

const STORAGE_KEY = "clinicdesk.thread";
let seq = 0;
const nextId = () => `m${Date.now().toString(36)}${(seq++).toString(36)}`;

export const initialState = (language: Lang = "en"): State => ({
  threadId: null, language, messages: [], status: null, busy: false, card: null, error: null, spoken: null,
});

export function reducer(state: State, a: Action): State {
  switch (a.type) {
    case "restore":
      return { ...state, threadId: a.threadId, messages: a.messages, card: a.card, language: a.language };
    case "language":
      return { ...state, language: a.language };
    case "user":
      return { ...state, busy: true, error: null, status: null, card: null,
               messages: [...state.messages, { id: nextId(), role: "user", text: a.display ?? a.text }] };
    case "started":
      return { ...state, threadId: a.threadId };
    case "status":
      return { ...state, status: a.text };
    case "card":
      return { ...state, card: a.card };
    case "finished": {
      const r = a.result;
      const text = r.ui?.text !== undefined ? r.ui.text : r.reply ?? "";
      const msgs = text || r.ui?.options || r.ui?.booked || r.ui?.emergency
        ? [...state.messages, { id: nextId(), role: "assistant" as const, text, ui: r.ui }]
        : state.messages;
      return { ...state, busy: false, status: null, card: r.interrupt ?? null, messages: msgs,
               language: (r.language as Lang) || state.language,
               spoken: { n: (state.spoken?.n ?? 0) + 1, text: r.speech || text || r.interrupt?.summary || "" } };
    }
    case "error":
      return { ...state, busy: false, status: null, error: a.error };
    case "reset":
      return initialState(state.language);
  }
}

/** The conversation this browser owns: its id and the secret key the clinic gave it (DEVLOG #48). */
interface Stored { id: string; key: string }
let current: Stored | null = null;
function readStored(): Stored | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    const v = raw ? JSON.parse(raw) : null;
    return v && typeof v.id === "string" && typeof v.key === "string" ? v : null;
  } catch { return null; }
}
function store(v: Stored | null) {
  current = v;
  try { v ? localStorage.setItem(STORAGE_KEY, JSON.stringify(v)) : localStorage.removeItem(STORAGE_KEY); } catch { /* private mode */ }
}
const keyHeader = (): Record<string, string> => (current ? { "X-Thread-Key": current.key } : {});

export function useConversation(defaultLang: Lang = "en") {
  const [state, dispatch] = useReducer(reducer, initialState(defaultLang));
  const stateRef = useRef(state);
  stateRef.current = state;

  // Pick the conversation back up after a reload.
  useEffect(() => {
    const saved = readStored();
    if (!saved || new URLSearchParams(window.location.search).get("say")) return;   // a scenario starts fresh
    current = saved;
    getJson<{ messages: { role: "user" | "assistant"; content: string }[]; interrupt: Card | null; language: Lang }>(
      `/chat/${saved.id}/history`, keyHeader(),
    ).then((h) => dispatch({
      type: "restore", threadId: saved.id, card: h.interrupt, language: h.language || defaultLang,
      messages: h.messages.map((m) => ({ id: nextId(), role: m.role, text: m.content })),
    })).catch(() => store(null));
  }, [defaultLang]);

  const run = useCallback(async (path: string, body: Record<string, unknown>) => {
    let last: RunResult | null = null;
    try {
      await postStream(`${API_URL}${path}`, body, (ev) => {
        const t = ev.type as string;
        if (t === "RUN_STARTED" && typeof ev.threadId === "string") {
          dispatch({ type: "started", threadId: ev.threadId });
        } else if (t === "CUSTOM" && ev.name === "status") {
          dispatch({ type: "status", text: String((ev.value as { text?: string })?.text ?? "") });
        } else if (t === "RUN_FINISHED") {
          last = ev.result as RunResult;           // with a typed answer to a card, only the last run counts
        } else if (t === "RUN_ERROR") {
          throw new StreamError(500, String(ev.message ?? ""));
        }
      }, undefined, keyHeader(), (res) => {
        const key = res.headers.get("X-Thread-Key"), id = res.headers.get("X-Thread-Id");
        if (key && id) store({ id, key });               // a new conversation: keep its secret
      });
      if (last) dispatch({ type: "finished", result: last });
      else dispatch({ type: "error", error: "network" });
    } catch (e) {
      dispatch({ type: "error", error: e instanceof StreamError && e.status === 429 ? "busy" : "network" });
    }
  }, []);

  /** A message typed or spoken. If a card is waiting, it answers the card.
   *  `display` is what the patient's bubble shows (e.g. the time they tapped instead of "2").
   *  `channel` "voice" tells the backend the text came from speech recognition. */
  const say = useCallback((text: string, display?: string, channel: Channel = "web") => {
    const s = stateRef.current;
    const clean = text.trim();
    if (!clean || s.busy) return;
    dispatch({ type: "user", text: clean, display });
    if (s.card && s.threadId) return run("/chat/answer/stream", { thread_id: s.threadId, text: clean, channel });
    return run("/chat/stream", { text: clean, thread_id: s.threadId, language: s.language, channel });
  }, [run]);

  /** A tap on a card's button. */
  const answer = useCallback((approved: boolean, label: string, channel: Channel = "web") => {
    const s = stateRef.current;
    if (!s.card || !s.threadId || s.busy) return;
    dispatch({ type: "user", text: label });
    return run("/chat/answer/stream", { thread_id: s.threadId, approved, channel });
  }, [run]);

  const setLanguage = useCallback((language: Lang) => dispatch({ type: "language", language }), []);
  const reset = useCallback(() => { store(null); dispatch({ type: "reset" }); }, []);

  return { state, say, answer, setLanguage, reset };
}
