const DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"] as const;
const SHORT: Record<string, string> = { mon: "Mon", tue: "Tue", wed: "Wed", thu: "Thu", fri: "Fri", sat: "Sat", sun: "Sun" };

function clock(hhmm: string): string {
  const [h, m] = hhmm.split(":").map(Number);
  const suffix = h >= 12 ? "pm" : "am";
  const h12 = h % 12 || 12;
  return m ? `${h12}:${String(m).padStart(2, "0")}${suffix}` : `${h12}${suffix}`;
}

/** {"mon": [["09:00","13:00"],["17:00","20:00"]], ...} → ["Mon–Sat 9am–1pm, 5pm–8pm"] */
export function formatHours(hours: Record<string, string[][]>): string[] {
  const groups: { days: string[]; ranges: string }[] = [];
  for (const d of DAYS) {
    const r = hours[d];
    if (!r || !r.length) continue;
    const ranges = r.map(([a, b]) => `${clock(a)}–${clock(b)}`).join(", ");
    const prev = groups[groups.length - 1];
    const prevIdx = prev ? DAYS.indexOf(prev.days[prev.days.length - 1] as (typeof DAYS)[number]) : -2;
    if (prev && prev.ranges === ranges && DAYS.indexOf(d) === prevIdx + 1) prev.days.push(d);
    else groups.push({ days: [d], ranges });
  }
  return groups.map((g) => {
    const days = g.days.length > 2 ? `${SHORT[g.days[0]]}–${SHORT[g.days[g.days.length - 1]]}` : g.days.map((x) => SHORT[x]).join(", ");
    return `${days} ${g.ranges}`;
  });
}
