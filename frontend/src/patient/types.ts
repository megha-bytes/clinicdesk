export type Lang = "en" | "hi" | "kn";

export interface SlotOption { n: number; when: string; doctor?: string; fee_inr?: number }
export interface Booked { doctor: string; when: string; fee_inr: number; pay_url?: string | null; token_no?: number | null; type?: string }
export interface Emergency { kind: string; numbers: Record<string, string> }

export interface Ui {
  text?: string;
  options_title?: string;
  options?: SlotOption[];
  booked?: Booked;
  chosen?: string;
  ask?: string[];
  emergency?: Emergency;
  refused?: string;
  handoff?: boolean;
}

export interface CardDetails { action?: string; doctor?: string; when?: string; new_when?: string; type?: string; fee_inr?: number; token_no?: number | null }
export interface Card { kind: "consent" | "confirm"; action: string; summary: string; details?: CardDetails }

export interface Message { id: string; role: "user" | "assistant"; text: string; ui?: Ui }

export interface RunResult { reply: string | null; speech?: string | null; interrupt: Card | null; ui?: Ui; language?: Lang | null }

/** How the patient is talking to the clinic: typing (web) or the call screen (voice). */
export type Channel = "web" | "voice";
