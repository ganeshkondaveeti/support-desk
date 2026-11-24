const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

/** "2026-07-04" → "04-Jul-2026". Leaves already-formatted values ("01-Sep-2026") alone. */
export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (!match) return value;
  const [, y, m, d] = match;
  return `${d}-${MONTHS[Number(m) - 1] ?? m}-${y}`;
}

/** "2026-11-24T21:02:04" → "24-Nov 21:02" (server times are shown as sent). */
export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})/.exec(value);
  if (!match) return value;
  const [, , m, d, hh, mm] = match;
  return `${d}-${MONTHS[Number(m) - 1] ?? m} ${hh}:${mm}`;
}

/** Window label with both ends, e.g. "12-Apr-2026 → 04-Jul-2026". */
export function formatWindow(start: string | null | undefined, end: string | null | undefined): string {
  return `${formatDate(start)} → ${formatDate(end)}`;
}

/** Split a "2026-11-18 → 2026-11-25" window string from the API and format both ends. */
export function formatWindowString(value: string | null | undefined): string {
  if (!value) return "—";
  const [start, end] = value.split("→").map((part) => part.trim());
  return end ? formatWindow(start, end) : formatDate(start);
}

export function shortId(value: string, keep = 14): string {
  return value.length > keep ? `${value.slice(0, keep)}…` : value;
}

export function countSentences(text: string): number {
  const parts = text.split(/(?<=[.!?])\s+/).filter((part) => part.trim().length > 0);
  return parts.length;
}

export function plural(n: number, one: string, many = `${one}s`): string {
  return `${n.toLocaleString("en-IN")} ${n === 1 ? one : many}`;
}

/** Parse "01-Sep-2026" or "2026-09-01" into a UTC date. */
export function parseDay(value: string | null | undefined): Date | null {
  if (!value) return null;
  const iso = /^(\d{4})-(\d{2})-(\d{2})/.exec(value);
  if (iso) return new Date(Date.UTC(Number(iso[1]), Number(iso[2]) - 1, Number(iso[3])));
  const dmy = /^(\d{2})-([A-Za-z]{3})-(\d{4})$/.exec(value);
  if (dmy) {
    const month = MONTHS.indexOf(dmy[2] ?? "");
    if (month >= 0) return new Date(Date.UTC(Number(dmy[3]), month, Number(dmy[1])));
  }
  return null;
}

export const STALE_AFTER_DAYS = 30;

/** Whole days between a "last checked" date and now; null when unparseable. */
export function daysSince(value: string | null | undefined, now: Date = new Date()): number | null {
  const day = parseDay(value);
  if (!day) return null;
  return Math.floor((now.getTime() - day.getTime()) / 86_400_000);
}
