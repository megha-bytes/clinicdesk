import { describe, expect, it } from "vitest";
import { parseSseChunk } from "../realtime/sse";
import { initialState, reducer } from "../patient/useConversation";
import { formatHours } from "../patient/hours";

describe("parseSseChunk", () => {
  it("splits complete events and keeps the partial rest", () => {
    const { events, rest } = parseSseChunk('data: {"type":"RUN_STARTED","threadId":"t"}\n\ndata: {"type":"CUS');
    expect(events).toEqual([{ type: "RUN_STARTED", threadId: "t" }]);
    expect(rest).toBe('data: {"type":"CUS');
  });
  it("ignores comments and keep-alives", () => {
    expect(parseSseChunk(": keep-alive\n\n").events).toEqual([]);
  });
});

describe("conversation reducer", () => {
  it("shows the option-free text and keeps the card from the run", () => {
    let s = reducer(initialState(), { type: "user", text: "Book Dr Rao" });
    expect(s.busy).toBe(true);
    s = reducer(s, { type: "status", text: "Checking Dr. Ananya Rao's calendar…" });
    expect(s.status).toContain("calendar");
    s = reducer(s, { type: "finished", result: { reply: "Free with…\n1. 9:20 AM\nReply with a number.\nYour name and mobile number?",
      interrupt: null, ui: { text: "Your name and mobile number?", options: [{ n: 1, when: "Wed 21 Oct, 9:20 AM" }] } } });
    expect(s.busy).toBe(false);
    expect(s.status).toBeNull();
    expect(s.messages[1].text).toBe("Your name and mobile number?");
    expect(s.messages[1].ui?.options?.[0].n).toBe(1);
  });
  it("adds no empty assistant message", () => {
    const s = reducer(initialState(), { type: "finished", result: { reply: null, interrupt: { kind: "consent", action: "register_patient", summary: "OK?" } } });
    expect(s.messages).toHaveLength(0);
    expect(s.card?.kind).toBe("consent");
  });
  it("shows the tapped time in the patient's bubble", () => {
    const s = reducer(initialState(), { type: "user", text: "2", display: "Wed 21 Oct, 9:50 AM" });
    expect(s.messages[0].text).toBe("Wed 21 Oct, 9:50 AM");
  });
});

describe("formatHours", () => {
  it("groups consecutive days with the same hours", () => {
    const day = [["09:00", "13:00"], ["17:00", "20:00"]];
    expect(formatHours({ mon: day, tue: day, wed: day, thu: day, fri: day, sat: day })).toEqual(["Mon–Sat 9am–1pm, 5pm–8pm"]);
    expect(formatHours({ mon: [["10:00", "13:00"]], wed: [["10:00", "13:00"]] })).toEqual(["Mon 10am–1pm", "Wed 10am–1pm"]);
  });
});
