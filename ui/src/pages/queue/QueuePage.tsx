import * as Dialog from "@radix-ui/react-dialog";
import { Inbox, ListPlus, MessageSquarePlus } from "lucide-react";
import { useMemo, useState } from "react";
import { useNavigate, useParams, useSearchParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useCreateTicket, useSeedTickets, useTicket, useTickets } from "../../api/hooks";
import { PageHeader } from "../../components/AppShell";
import { Badge, Banner, Button, EmptyState, ErrorState, LoadingBlock } from "../../components/ui";
import { PAGE } from "../../copy";
import { cx } from "../../lib/cx";
import { QueueList } from "./QueueList";
import { TicketPanel } from "./TicketPanel";

const STATUS_FILTERS = [
  { value: "", label: "All" },
  { value: "awaiting_human", label: "Awaiting review" },
  { value: "blocked", label: "Blocked" },
  { value: "sent", label: "Sent" },
  { value: "rejected", label: "Rejected" },
] as const;

const PRIORITIES = ["", "P1", "P2", "P3"] as const;

export default function QueuePage() {
  const { ticketId } = useParams();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const filters = { status: params.get("status") ?? "", team: params.get("team") ?? "", priority: params.get("priority") ?? "" };
  const tickets = useTickets(filters);
  const allTickets = useTickets({});
  const detail = useTicket(ticketId);
  const [submitOpen, setSubmitOpen] = useState(false);
  const seed = useSeedTickets();

  const teams = useMemo(
    () => Array.from(new Set((allTickets.data?.items ?? []).map((t) => t.route_team).filter((t): t is string => Boolean(t)))).sort(),
    [allTickets.data],
  );

  const setFilter = (key: string, value: string) => {
    const next = new URLSearchParams(params);
    if (value) next.set(key, value);
    else next.delete(key);
    setParams(next, { replace: true });
  };
  const open = (id: string) => navigate({ pathname: `/queue/${encodeURIComponent(id)}`, search: params.toString() });
  const closeDetail = () => navigate({ pathname: "/queue", search: params.toString() });

  const items = tickets.data?.items ?? [];
  const blockedCount = (allTickets.data?.items ?? []).filter((t) => t.status === "blocked").length;

  return (
    <>
      <PageHeader
        title={PAGE.queue.title}
        subtitle={PAGE.queue.subtitle}
        meta={tickets.data ? <Badge mono>{tickets.data.total} shown</Badge> : null}
        actions={
          <>
            <Button icon={ListPlus} busy={seed.isPending} onClick={() => seed.mutate()}>
              Load seed questions
            </Button>
            <Button variant="primary" icon={MessageSquarePlus} onClick={() => setSubmitOpen(true)}>
              Submit question
            </Button>
          </>
        }
      />
      {seed.isSuccess ? <Banner tone="success">Queued {seed.data.enqueued} seed questions. They appear as the agents finish.</Banner> : null}
      {seed.error ? <ErrorState error={seed.error} /> : null}

      <div className="card flex flex-wrap items-center gap-x-4 gap-y-2 px-3 py-2">
        <div role="group" aria-label="Filter by status" className="flex flex-wrap items-center gap-1">
          <span className="mr-1 text-label-sm uppercase text-ink-faint">Status</span>
          {STATUS_FILTERS.map(({ value, label }) => (
            <FilterChip key={label} active={filters.status === value} onClick={() => setFilter("status", value)}>
              {label}
              {value === "blocked" && blockedCount > 0 ? ` (${blockedCount})` : ""}
            </FilterChip>
          ))}
        </div>
        <label className="flex items-center gap-2">
          <span className="text-label-sm uppercase text-ink-faint">Team</span>
          <select
            value={filters.team}
            onChange={(event) => setFilter("team", event.target.value)}
            className="h-8 max-w-[16rem] rounded border border-line-strong bg-surface px-2 text-body-md focus:border-primary focus:outline-none"
          >
            <option value="">All teams</option>
            {teams.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <div role="group" aria-label="Filter by priority" className="flex items-center gap-1">
          <span className="mr-1 text-label-sm uppercase text-ink-faint">Priority</span>
          {PRIORITIES.map((p) => (
            <FilterChip key={p || "all"} active={filters.priority === p} onClick={() => setFilter("priority", p)}>
              {p || "All"}
            </FilterChip>
          ))}
        </div>
      </div>

      <div className={cx("grid grid-cols-1 gap-4", ticketId && "xl:grid-cols-[minmax(0,3fr)_minmax(420px,2fr)]")}>
        <div className={cx(ticketId && "hidden lg:block")}>
          {tickets.isLoading ? (
            <LoadingBlock rows={8} label="Loading tickets" />
          ) : tickets.error ? (
            <ErrorState error={tickets.error} onRetry={() => void tickets.refetch()} />
          ) : items.length === 0 ? (
            <EmptyState
              icon={Inbox}
              title={filters.status || filters.team || filters.priority ? "No tickets match these filters" : "No tickets yet"}
              action={
                <>
                  <Button icon={ListPlus} onClick={() => seed.mutate()}>
                    Load seed questions
                  </Button>
                  <Button variant="primary" icon={MessageSquarePlus} onClick={() => setSubmitOpen(true)}>
                    Submit a question
                  </Button>
                </>
              }
            />
          ) : (
            <QueueList items={items} selectedId={ticketId} onSelect={open} compact={Boolean(ticketId)} />
          )}
        </div>

        {/* ≥1280px: split pane. 1024–1279px: 380px overlay sheet. <1024px: full width (plan P9.11). */}
        {ticketId ? (
          <div
            className={cx(
              "xl:sticky xl:top-[calc(var(--header-h)+1.25rem)] xl:max-h-[calc(100vh-var(--header-h)-2.5rem)] xl:overflow-y-auto",
              "lg:max-xl:fixed lg:max-xl:bottom-0 lg:max-xl:right-0 lg:max-xl:top-header lg:max-xl:z-30 lg:max-xl:w-[380px] lg:max-xl:overflow-y-auto lg:max-xl:border-l lg:max-xl:border-line lg:max-xl:bg-canvas lg:max-xl:p-3 lg:max-xl:shadow-2",
            )}
          >
            {detail.isLoading ? (
              <LoadingBlock rows={10} label="Loading ticket" />
            ) : detail.error ? (
              <ErrorState
                error={detail.error instanceof ApiError && detail.error.status === 404 ? new Error("This ticket does not exist.") : detail.error}
                onRetry={() => void detail.refetch()}
              />
            ) : detail.data ? (
              <TicketPanel ticket={detail.data} onClose={closeDetail} />
            ) : null}
          </div>
        ) : null}
      </div>

      <SubmitQuestionDialog open={submitOpen} onOpenChange={setSubmitOpen} onCreated={open} />
    </>
  );
}

function FilterChip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cx(
        "h-7 rounded-full border px-2.5 text-body-sm",
        active ? "border-primary bg-primary text-white" : "border-line bg-surface text-ink-muted hover:border-line-strong hover:text-ink",
      )}
    >
      {children}
    </button>
  );
}

function SubmitQuestionDialog({ open, onOpenChange, onCreated }: { open: boolean; onOpenChange: (open: boolean) => void; onCreated: (id: string) => void }) {
  const [text, setText] = useState("");
  const create = useCreateTicket();
  const submit = () =>
    create.mutate(text, {
      onSuccess: (ticket) => {
        // Drop the typed text immediately; the detail view shows the redacted copy from the server.
        setText("");
        onOpenChange(false);
        onCreated(ticket.id);
      },
    });
  return (
    <Dialog.Root
      open={open}
      onOpenChange={(next) => {
        if (!next) {
          setText("");
          create.reset();
        }
        onOpenChange(next);
      }}
    >
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-ink/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 rounded-md border border-line bg-surface p-5 shadow-2">
          <Dialog.Title className="text-headline-sm">Submit a question</Dialog.Title>
          <Dialog.Description className="mt-1 text-body-md text-ink-muted">
            Personal details like PAN, phone or email are redacted automatically before anything is stored or sent to a model.
          </Dialog.Description>
          <label htmlFor="question" className="sr-only">
            Question
          </label>
          <textarea
            id="question"
            rows={5}
            value={text}
            onChange={(event) => setText(event.target.value)}
            maxLength={4000}
            className="mt-3 w-full rounded border border-line-strong p-2.5 text-body-lg font-normal focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
            placeholder="Why was I charged an exit load?"
          />
          {create.error ? (
            <Banner tone="critical" className="mt-2">
              {create.error instanceof Error ? create.error.message : "The request failed."}
            </Banner>
          ) : null}
          <div className="mt-4 flex justify-end gap-2">
            <Dialog.Close asChild>
              <Button disabled={create.isPending}>Cancel</Button>
            </Dialog.Close>
            <Button variant="primary" busy={create.isPending} disabled={!text.trim()} onClick={submit}>
              Submit
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
