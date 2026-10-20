import { useEffect, useState } from "react";
import { getJson } from "../realtime/api";
import { formatHours } from "./hours";
import { tr } from "./i18n";
import type { Lang } from "./types";

interface ClinicInfo {
  name: string; address?: string; emergency_numbers: Record<string, string>;
  doctors: { name: string; specialty: string; hours: Record<string, string[][]> }[];
}

export function useClinic() {
  const [info, setInfo] = useState<ClinicInfo | null>(null);
  useEffect(() => { getJson<ClinicInfo>("/chat/clinic").then(setInfo).catch(() => setInfo(null)); }, []);
  return info;
}

export function ClinicPanel({ info, lang }: { info: ClinicInfo | null; lang: Lang }) {
  if (!info) return null;
  const n = info.emergency_numbers;
  return (
    <div className="text-[15px]">
      <h1 className="text-2xl font-bold leading-tight">{info.name}</h1>
      {info.address && <p className="mt-1 text-ink-soft">{info.address}</p>}
      <h2 className="mt-8 font-semibold">{tr("hours", lang)}</h2>
      <ul className="mt-2 space-y-3">
        {info.doctors.map((d) => (
          <li key={d.name}>
            <p className="font-medium">{d.name} <span className="font-normal text-ink-soft">{d.specialty}</span></p>
            {formatHours(d.hours).map((h) => <p key={h} className="text-ink-soft tabular">{h}</p>)}
          </li>
        ))}
      </ul>
      <h2 className="mt-8 font-semibold">{tr("emergencyNumbers", lang)}</h2>
      <p className="mt-1 text-ink-soft">
        <a className="font-semibold text-alarm" href={`tel:${n.ambulance}`}>{n.ambulance}</a> ambulance,{" "}
        <a className="font-semibold text-alarm" href={`tel:${n.emergency}`}>{n.emergency}</a> emergency,{" "}
        <a className="font-semibold text-alarm" href={`tel:${n.mental_health}`}>{n.mental_health}</a> Tele-MANAS
      </p>
    </div>
  );
}
