import { Clock } from "lucide-react";

import { daysSince, STALE_AFTER_DAYS } from "../lib/format";
import { Badge } from "./ui";

/** Staleness badge when a date is more than 30 days old (arch §17.2, EC-CO-16). */
export function StaleBadge({ date }: { date: string | null | undefined }) {
  const days = daysSince(date);
  if (days === null || days <= STALE_AFTER_DAYS) return null;
  return (
    <Badge tone="warning" title={`Captured ${days} days ago. Figures may have changed since.`}>
      <Clock aria-hidden className="h-3 w-3" />
      {days} days old
    </Badge>
  );
}

/** "Last checked 01-Sep-2026 · Snapshot B" with a staleness badge. */
export function LastChecked({ date, snapshot }: { date: string | null | undefined; snapshot?: string | null }) {
  if (!date) return <span className="text-body-sm text-ink-faint">Last checked: not recorded</span>;
  return (
    <span className="inline-flex flex-wrap items-center gap-1.5 text-body-sm text-ink-muted">
      <span>
        Last checked <time className="font-mono text-label-md text-ink">{date}</time>
        {snapshot ? <> · Snapshot {snapshot}</> : null}
      </span>
      <StaleBadge date={date} />
    </span>
  );
}
