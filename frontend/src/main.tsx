import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "@fontsource/hind/devanagari-400.css";
import "@fontsource/hind/devanagari-500.css";
import "@fontsource/hind/devanagari-600.css";
import "@fontsource/hind/devanagari-700.css";
import "@fontsource/hind/latin-400.css";
import "@fontsource/hind/latin-500.css";
import "@fontsource/hind/latin-600.css";
import "@fontsource/hind/latin-700.css";
import "@fontsource/noto-sans-kannada/kannada-400.css";
import "@fontsource/noto-sans-kannada/kannada-600.css";
import "./index.css";
import PatientApp from "./patient/PatientApp";
import StaffApp from "./staff/StaffApp";
import Landing from "./landing/Landing";

// /  judge landing page · /app  patient app (chat + call) · /staff  staff dashboard (login)
const path = window.location.pathname;
const App = path.startsWith("/staff") ? StaffApp : path.startsWith("/app") ? PatientApp : Landing;
document.title = path.startsWith("/staff") ? "ClinicDesk · Staff dashboard"
  : path.startsWith("/app") ? "ClinicDesk · Sunrise Family Clinic" : "ClinicDesk · the clinic front desk you can trust";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
