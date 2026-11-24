import { useRef, type KeyboardEvent } from "react";

import type { TicketListItem } from "../../api/types";
import { RedactedText } from "../../components/RedactedText";
import { PriorityPill, StatusPill } from "../../components/ui";
import { TICKET_STATUS } from "../../copy";
import { cx } from "../../lib/cx";
import { formatDateTime } from "../../lib/format";

/**
 * Ticket table. Rows are keyboard-selectable: ↑/↓ move focus, Enter opens (plan P9.3).
 */
export function QueueList({
  items,
  selectedId,
  onSelect,
  compact = false,
}: {
  items: TicketListItem[];
  selectedId: string | undefined;
  onSelect: (id: string) => void;
  /** With the detail pane open, Team and Flags move to the detail view. */
  compact?: boolean;
}) {
  const wide = (cls: string) => cx(cls, compact && "hidden 2xl:table-cell");
  const body = useRef<HTMLTableSectionElement>(null);

  const onKeyDown = (event: KeyboardEvent<HTMLTableRowElement>, id: string) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      onSelect(id);
      return;
    }
    if (event.key !== "ArrowDown" && event.key !== "ArrowUp") return;
    event.preventDefault();
    const rows = Array.from(body.current?.querySelectorAll<HTMLTableRowElement>("tr[data-row]") ?? []);
    const index = rows.indexOf(event.currentTarget);
    const next = rows[index + (event.key === "ArrowDown" ? 1 : -1)];
    next?.focus();
  };

  return (
    <div className="card overflow-x-auto">
      <table className={cx("w-full table-fixed border-collapse text-left", compact ? "min-w-[520px]" : "min-w-[760px]")}>
        <caption className="sr-only">Support tickets. Use arrow keys to move and Enter to open a ticket.</caption>
        {/* Fixed layout: the question column takes what's left and truncates. */}
        <colgroup>
          <col className="w-[118px]" />
          <col />
          <col className="w-[148px]" />
          <col className="w-[76px]" />
          <col className={wide("w-[200px]")} />
          <col className={wide("w-[88px]")} />
        </colgroup>
        <thead className="sticky top-0 z-10 bg-canvas/95 backdrop-blur">
          <tr className="border-b border-line">
            {["Received", "Question", "Status", "Priority", "Team", "Flags"].map((h) => (
              <th
                key={h}
                scope="col"
                className={(h === "Team" || h === "Flags" ? wide : cx)("whitespace-nowrap px-3 py-2 text-label-sm font-semibold uppercase tracking-wide text-ink-faint")}
              >
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody ref={body}>
          {items.map((t) => {
            const selected = t.id === selectedId;
            const flags = t.pii_types_found.length;
            return (
              <tr
                key={t.id}
                data-row
                tabIndex={0}
                aria-selected={selected}
                onClick={() => onSelect(t.id)}
                onKeyDown={(event) => onKeyDown(event, t.id)}
                className={cx(
                  "h-11 cursor-pointer border-b border-line/70 align-middle last:border-0 hover:bg-primary-wash/60 focus:bg-primary-wash focus:outline-none",
                  selected && "bg-primary-wash",
                  t.status === "blocked" && !selected && "bg-critical-bg/40",
                )}
              >
                <td className="whitespace-nowrap px-3 font-mono text-label-md text-ink-muted">
                  <time dateTime={t.received_at}>{formatDateTime(t.received_at)}</time>
                </td>
                <td className="px-3">
                  <p className="truncate text-body-md text-ink" title={t.question_redacted}>
                    <RedactedText text={t.question_redacted} />
                  </p>
                </td>
                <td className="whitespace-nowrap px-3">
                  <StatusPill status={t.status} label={TICKET_STATUS[t.status]} />
                </td>
                <td className="px-3">
                  <PriorityPill priority={t.priority} />
                </td>
                <td className={wide("max-w-[12rem] truncate px-3 text-body-sm text-ink-muted")} title={t.route_team ?? undefined}>
                  {t.route_team ?? "—"}
                </td>
                <td className={wide("whitespace-nowrap px-3 font-mono text-label-sm text-ink-muted")}>
                  {flags > 0 ? <span title={`Redacted: ${t.pii_types_found.join(", ")}`}>PII×{flags}</span> : null}
                  {t.refinement_count > 0 ? <span className="ml-1.5">ref {t.refinement_count}</span> : null}
                  {flags === 0 && t.refinement_count === 0 ? "—" : null}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
