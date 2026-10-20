import type { Lang } from "./types";

/** Interface wording (the assistant's own replies come from the backend in the patient's language). */
const S = {
  title: { en: "Front desk", hi: "रिसेप्शन", kn: "ಸ್ವಾಗತ ಕೌಂಟರ್" },
  welcome: {
    en: "Namaste! I can book you an appointment, or tell you the timings and fees. You can write in English, Hindi or Kannada.",
    hi: "नमस्ते! मैं आपकी अपॉइंटमेंट बुक कर सकता हूँ, या समय और फ़ीस बता सकता हूँ। आप हिंदी, अंग्रेज़ी या कन्नड़ में लिख सकते हैं।",
    kn: "ನಮಸ್ಕಾರ! ನಿಮ್ಮ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಬಹುದು, ಅಥವಾ ಸಮಯ ಮತ್ತು ಶುಲ್ಕ ತಿಳಿಸಬಹುದು. ಕನ್ನಡ, ಹಿಂದಿ ಅಥವಾ ಇಂಗ್ಲಿಷ್‌ನಲ್ಲಿ ಬರೆಯಿರಿ.",
  },
  placeholder: { en: "Type your message", hi: "अपना संदेश लिखें", kn: "ನಿಮ್ಮ ಸಂದೇಶ ಬರೆಯಿರಿ" },
  placeholderCard: { en: "Or type your answer", hi: "या अपना जवाब लिखें", kn: "ಅಥವಾ ನಿಮ್ಮ ಉತ್ತರ ಬರೆಯಿರಿ" },
  send: { en: "Send", hi: "भेजें", kn: "ಕಳುಹಿಸಿ" },
  newChat: { en: "New conversation", hi: "नई बातचीत", kn: "ಹೊಸ ಸಂಭಾಷಣೆ" },
  agree: { en: "I agree", hi: "मैं सहमत हूँ", kn: "ನಾನು ಒಪ್ಪುತ್ತೇನೆ" },
  disagree: { en: "No, thanks", hi: "नहीं, धन्यवाद", kn: "ಬೇಡ, ಧನ್ಯವಾದ" },
  yesBook: { en: "Yes, book it", hi: "हाँ, बुक करें", kn: "ಹೌದು, ಬುಕ್ ಮಾಡಿ" },
  yesMove: { en: "Yes, move it", hi: "हाँ, बदलें", kn: "ಹೌದು, ಬದಲಿಸಿ" },
  yesCancel: { en: "Yes, cancel it", hi: "हाँ, रद्द करें", kn: "ಹೌದು, ರದ್ದು ಮಾಡಿ" },
  no: { en: "No", hi: "नहीं", kn: "ಬೇಡ" },
  confirmTitle: { en: "Book this appointment?", hi: "यह अपॉइंटमेंट बुक करें?", kn: "ಈ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬುಕ್ ಮಾಡಲೇ?" },
  moveTitle: { en: "Move your appointment?", hi: "अपॉइंटमेंट बदलें?", kn: "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ಬದಲಿಸಲೇ?" },
  cancelTitle: { en: "Cancel your appointment?", hi: "अपॉइंटमेंट रद्द करें?", kn: "ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್ ರದ್ದು ಮಾಡಲೇ?" },
  consentTitle: { en: "Before we save your details", hi: "आपकी जानकारी सेव करने से पहले", kn: "ನಿಮ್ಮ ವಿವರ ಉಳಿಸುವ ಮೊದಲು" },
  booked: { en: "Booked", hi: "बुक", kn: "ಬುಕ್" },
  token: { en: "Token", hi: "टोकन", kn: "ಟೋಕನ್" },
  fee: { en: "Fee", hi: "फ़ीस", kn: "ಶುಲ್ಕ" },
  pay: { en: "Pay ₹{fee} now", hi: "₹{fee} अभी भुगतान करें", kn: "₹{fee} ಈಗ ಪಾವತಿಸಿ" },
  payLater: { en: "You can also pay at the clinic.", hi: "आप क्लिनिक में भी भुगतान कर सकते हैं।", kn: "ಕ್ಲಿನಿಕ್‌ನಲ್ಲೂ ಪಾವತಿಸಬಹುದು." },
  emergencyTitle: { en: "This could be an emergency", hi: "यह इमरजेंसी हो सकती है", kn: "ಇದು ತುರ್ತು ಪರಿಸ್ಥಿತಿ ಆಗಿರಬಹುದು" },
  selfHarmTitle: { en: "You don't have to go through this alone", hi: "आप अकेले नहीं हैं", kn: "ನೀವು ಒಬ್ಬರೇ ಇಲ್ಲ" },
  callAmbulance: { en: "Call {n} for an ambulance", hi: "एम्बुलेंस के लिए {n} पर कॉल करें", kn: "ಆಂಬ್ಯುಲೆನ್ಸ್‌ಗೆ {n} ಗೆ ಕರೆ ಮಾಡಿ" },
  callEmergency: { en: "Call {n} for emergency help", hi: "इमरजेंसी मदद के लिए {n} पर कॉल करें", kn: "ತುರ್ತು ಸಹಾಯಕ್ಕೆ {n} ಗೆ ಕರೆ ಮಾಡಿ" },
  callMental: { en: "Call Tele-MANAS on {n} (free, any time)", hi: "Tele-MANAS को {n} पर कॉल करें (मुफ़्त, कभी भी)", kn: "Tele-MANAS ಗೆ {n} ಗೆ ಕರೆ ಮಾಡಿ (ಉಚಿತ, ಯಾವಾಗ ಬೇಕಾದರೂ)" },
  staffAlerted: { en: "The clinic staff have been alerted.", hi: "क्लिनिक स्टाफ़ को बता दिया गया है।", kn: "ಕ್ಲಿನಿಕ್ ಸಿಬ್ಬಂದಿಗೆ ತಿಳಿಸಲಾಗಿದೆ." },
  notUrgent: { en: "It isn't urgent", hi: "अर्जेंट नहीं है", kn: "ತುರ್ತು ಅಲ್ಲ" },
  backToChat: { en: "Back to the conversation", hi: "बातचीत पर वापस", kn: "ಸಂಭಾಷಣೆಗೆ ಹಿಂದಿರುಗಿ" },
  handoff: { en: "A staff member will call you back.", hi: "स्टाफ़ आपको वापस कॉल करेगा।", kn: "ಸಿಬ್ಬಂದಿ ನಿಮಗೆ ಮರಳಿ ಕರೆ ಮಾಡುತ್ತಾರೆ." },
  errorNetwork: { en: "Couldn't reach the clinic. Check your connection and send again.", hi: "क्लिनिक से जुड़ नहीं पाए। इंटरनेट देखें और फिर भेजें।", kn: "ಕ್ಲಿನಿಕ್ ಸಂಪರ್ಕ ಸಿಗಲಿಲ್ಲ. ಸಂಪರ್ಕ ಪರಿಶೀಲಿಸಿ ಮತ್ತೆ ಕಳುಹಿಸಿ." },
  errorBusy: { en: "Too many messages. Wait a few minutes and send again.", hi: "बहुत सारे संदेश। कुछ मिनट रुककर फिर भेजें।", kn: "ಹಲವು ಸಂದೇಶಗಳು. ಕೆಲವು ನಿಮಿಷ ಕಾಯಿರಿ." },
  hours: { en: "Doctors and hours", hi: "डॉक्टर और समय", kn: "ವೈದ್ಯರು ಮತ್ತು ಸಮಯ" },
  emergencyNumbers: { en: "In an emergency", hi: "इमरजेंसी में", kn: "ತುರ್ತು ಸಂದರ್ಭದಲ್ಲಿ" },
  closed: { en: "Closed", hi: "बंद", kn: "ಮುಚ್ಚಿದೆ" },
  suggestBook: { en: "Book with Dr. Rao tomorrow", hi: "कल डॉ. राव से मिलना है", kn: "ನಾಳೆ ಡಾ. ರಾವ್ ಜೊತೆ ಅಪಾಯಿಂಟ್‌ಮೆಂಟ್" },
  suggestHours: { en: "What are the timings?", hi: "क्लिनिक का समय क्या है?", kn: "ಕ್ಲಿನಿಕ್ ಸಮಯ ಏನು?" },
  suggestSkin: { en: "I need a skin doctor", hi: "स्किन के डॉक्टर चाहिए", kn: "ಚರ್ಮದ ವೈದ್ಯರು ಬೇಕು" },
} as const;

export type Key = keyof typeof S;

export function tr(key: Key, lang: Lang, vars: Record<string, string | number> = {}): string {
  let s: string = S[key][lang] ?? S[key].en;
  for (const [k, v] of Object.entries(vars)) s = s.replace(`{${k}}`, String(v));
  return s;
}
