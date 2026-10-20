// @vitest-environment jsdom
import { act, cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { initialState, reducer, type State } from "../patient/useConversation";
import type { useConversation } from "../patient/useConversation";
import { CallScreen } from "../voice/CallScreen";
import { pickVoice, type ListenHandlers, type SpeechEngine } from "../voice/engine";

afterEach(cleanup);

const voice = (lang: string, name = lang, localService = true) => ({ lang, name, localService }) as SpeechSynthesisVoice;

describe("pickVoice", () => {
  it("prefers the exact Indian locale and natural voices", () => {
    const vs = [voice("hi-IN", "Lekha", true), voice("hi-IN", "Google हिन्दी", false), voice("en-US"), voice("en-IN", "Rishi")];
    expect(pickVoice(vs, "hi")?.name).toBe("Google हिन्दी");
    expect(pickVoice(vs, "en")?.name).toBe("Rishi");
  });
  it("returns null when the device has no voice for the language", () => {
    expect(pickVoice([voice("hi-IN"), voice("en-IN")], "kn")).toBeNull();
    expect(pickVoice([voice("kn_IN", "Sapna")], "kn")?.name).toBe("Sapna");
  });
});

describe("reducer: spoken replies", () => {
  it("counts every run and prefers the backend's speech text", () => {
    let s = reducer(initialState(), { type: "user", text: "hi" });
    s = reducer(s, { type: "finished", result: { reply: "✅ Booked", speech: "Booked", interrupt: null } });
    expect(s.spoken).toEqual({ n: 1, text: "Booked" });
    s = reducer(s, { type: "finished", result: { reply: null, interrupt: { kind: "consent", action: "x", summary: "OK?" } } });
    expect(s.spoken).toEqual({ n: 2, text: "OK?" });
  });
});

// ------------------------------------------------------------------ the call screen with a fake speech engine
function fakeEngine(canSpeak = true) {
  const listeners: ListenHandlers[] = [];
  const spoken: string[] = [];
  let finishSpeech: (() => void) | null = null;
  const engine: SpeechEngine = {
    canListen: () => true,
    canSpeak: () => canSpeak,
    listen: vi.fn((_lang, h: ListenHandlers) => { listeners.push(h); return () => h.onEnd(); }),
    speak: vi.fn((text: string) => { spoken.push(text); return new Promise<boolean>((r) => { finishSpeech = () => r(true); }); }),
    stopSpeaking: vi.fn(() => finishSpeech?.()),
  };
  return {
    engine, listeners, spoken,
    async finishSpeaking() { await act(async () => { finishSpeech?.(); finishSpeech = null; }); },
    async hear(text: string) { const h = listeners[listeners.length - 1]; await act(async () => { h.onFinal(text); h.onEnd(); }); },
    async silence() { const h = listeners[listeners.length - 1]; await act(async () => { h.onError("no-speech"); h.onEnd(); }); },
  };
}

function fakeConv(state: Partial<State> = {}) {
  return {
    state: { ...initialState("en"), ...state },
    say: vi.fn(), answer: vi.fn(), setLanguage: vi.fn(), reset: vi.fn(),
  } as unknown as ReturnType<typeof useConversation>;
}

describe("CallScreen", () => {
  it("greets, listens, sends what was heard as voice, then reads the reply and listens again", async () => {
    const f = fakeEngine();
    const conv = fakeConv();
    const { rerender } = render(<CallScreen conv={conv} clinicName="Sunrise Family Clinic" onClose={() => {}} engine={f.engine} />);
    expect(f.spoken[0]).toBe("Namaste, Sunrise Family Clinic. How can I help you?");
    await f.finishSpeaking();
    expect(f.engine.listen).toHaveBeenCalledTimes(1);
    expect(screen.getByText("Listening…")).toBeTruthy();

    await f.hear("book with doctor Rao tomorrow");
    expect(conv.say).toHaveBeenCalledWith("book with doctor Rao tomorrow", undefined, "voice");

    const replied = { ...conv, state: { ...conv.state, spoken: { n: 1, text: "Free with Dr. Rao: Option 1: tomorrow, 9:30 AM. Say the option number." } } };
    rerender(<CallScreen conv={replied} clinicName="Sunrise Family Clinic" onClose={() => {}} engine={f.engine} />);
    expect(f.spoken[1]).toContain("Option 1");
    await f.finishSpeaking();
    expect(f.engine.listen).toHaveBeenCalledTimes(2);
  });

  it("asks again after silence, then waits for a tap", async () => {
    const f = fakeEngine();
    render(<CallScreen conv={fakeConv()} clinicName="C" onClose={() => {}} engine={f.engine} />);
    await f.finishSpeaking();
    await f.silence();
    expect(f.spoken[1]).toBe("Sorry, I didn't catch that. Please say it again.");
    await f.finishSpeaking();
    await f.silence();
    expect(f.engine.listen).toHaveBeenCalledTimes(2);
    expect(screen.getAllByText("Tap the mic to talk").length).toBeGreaterThan(0);
  });

  it("does not read out replies from before the call", async () => {
    const f = fakeEngine();
    render(<CallScreen conv={fakeConv({ spoken: { n: 3, text: "old reply" } })} clinicName="C" onClose={() => {}} engine={f.engine} />);
    expect(f.spoken).toEqual(["Namaste, C. How can I help you?"]);
  });

  it("shows replies on screen when the device has no voice for the language", async () => {
    const f = fakeEngine(false);
    render(<CallScreen conv={fakeConv({ language: "kn" })} clinicName="C" onClose={() => {}} engine={f.engine} />);
    await act(async () => {});
    expect(f.engine.speak).not.toHaveBeenCalled();
    expect(screen.getByText("ನಮಸ್ಕಾರ, C. ಹೇಳಿ, ಏನು ಸಹಾಯ ಬೇಕು?")).toBeTruthy();
    expect(screen.getByText(/ಕನ್ನಡ ಧ್ವನಿ ಇಲ್ಲ/)).toBeTruthy();
    expect(f.engine.listen).toHaveBeenCalledTimes(1);
  });
});
