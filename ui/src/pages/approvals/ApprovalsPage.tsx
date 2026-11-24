import * as Tabs from "@radix-ui/react-tabs";
import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, ClipboardCheck, FileText, Mail, Send, ShieldAlert, ShieldCheck, X } from "lucide-react";
import { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useActions, useDecideAction, useHealth, useTicket } from "../../api/hooks";
import type { Action, ReviewerResult } from "../../api/types";
import { PageHeader } from "../../components/AppShell";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { PlainMarkdown } from "../../components/PlainMarkdown";
import { Badge, Banner, Button, EmptyState, ErrorState, IdChip, LoadingBlock, StatusPill } from "../../components/ui";
import { ACTION_KIND, ACTION_STATUS, PAGE, SUBJECT } from "../../copy";
import { cx } from "../../lib/cx";
import { formatDateTime } from "../../lib/format";
import { normalizeHex, payloadMatchesHash } from "../../lib/safe";

const TABS = [
  { value: "pending", label: "Pending" },
  { value: "executed", label: "Written" },
  { value: "rejected", label: "Rejected" },
  { value: "superseded", label: "Superseded" },
  { value: "failed", label: "Failed" },
] as const;

const KIND_ORDER = ["doc_append", "email_draft", "reply_send"] as const;
const KIND_ICON = { doc_append: FileText, email_draft: Mail, reply_send: Send } as const;
const KIND_GROUP_NOTE: Record<string, string> = {
  doc_append: "Appends to a document through the MCP writer",
  email_draft: "Creates a draft only. Nothing is sent.",
  reply_send: "Writes the customer reply to the outbox",
};

export default function ApprovalsPage() {
  const [tab, setTab] = useState<string>("pending");
  const pending = useActions("pending");
  const list = useActions(tab);
  const location = useLocation();

  // Deep links from the pulse page: /approvals#<action id>.
  useEffect(() => {
    if (!location.hash || !list.data) return;
    const el = document.getElementById(decodeURIComponent(location.hash.slice(1)));
    el?.scrollIntoView({ block: "start" });
  }, [location.hash, list.data]);

  const items = list.data?.items ?? [];

  return (
    <>
      <PageHeader
        title={PAGE.approvals.title}
        subtitle={PAGE.approvals.subtitle}
        meta={<Badge tone="warning" mono>{pending.data?.total ?? 0} pending</Badge>}
      />
      <Tabs.Root value={tab} onValueChange={setTab}>
        <Tabs.List aria-label="Action status" className="mb-3 flex gap-4 overflow-x-auto border-b border-line">
          {TABS.map(({ value, label }) => (
            <Tabs.Trigger
              key={value}
              value={value}
              className="-mb-px inline-flex items-center gap-2 whitespace-nowrap border-b-2 border-transparent px-1 pb-2 text-body-lg text-ink-muted hover:text-ink data-[state=active]:border-primary data-[state=active]:text-ink"
            >
              {label}
              {value === "pending" ? <Badge mono>{pending.data?.total ?? 0}</Badge> : null}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
        <Tabs.Content value={tab} className="focus:outline-none">
      {list.isLoading ? (
        <LoadingBlock rows={8} label="Loading actions" />
      ) : list.error ? (
        <ErrorState error={list.error} onRetry={() => void list.refetch()} />
      ) : items.length === 0 ? (
        <EmptyState icon={ClipboardCheck} title={tab === "pending" ? "Nothing waiting for approval" : `No ${ACTION_STATUS[tab]?.toLowerCase() ?? tab} actions`}>
          {tab === "pending" ? "Every write and send needs your approval. They'll appear here." : null}
        </EmptyState>
      ) : (
        <div className="space-y-6">
          {KIND_ORDER.map((kind) => {
            const group = items.filter((a) => a.kind === kind);
            if (group.length === 0) return null;
            return (
              <section key={kind} aria-labelledby={`group-${kind}`}>
                <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
                  <h2 id={`group-${kind}`} className="text-headline-sm">
                    {ACTION_KIND[kind]} <Badge mono className="ml-1 align-middle">{group.length}</Badge>
                  </h2>
                  <p className="text-body-sm text-ink-faint">{KIND_GROUP_NOTE[kind]}</p>
                </div>
                <div className="space-y-3">
                  {group.map((a) => (
                    <ActionCard key={a.id} action={a} />
                  ))}
                </div>
              </section>
            );
          })}
        </div>
      )}
        </Tabs.Content>
      </Tabs.Root>
    </>
  );
}

type HashState = "checking" | "match" | "mismatch";

function useHashCheck(actionId: string, payload: string, hash: string): HashState {
  // The payload is frozen per action, so (id, hash) identifies the check.
  const { data, isError } = useQuery({
    queryKey: ["hash-check", actionId, hash],
    queryFn: () => payloadMatchesHash(payload, hash),
    staleTime: Infinity,
  });
  if (isError) return "mismatch";
  if (data === undefined) return "checking";
  return data ? "match" : "mismatch";
}

export function ActionCard({ action }: { action: Action }) {
  const { data: health } = useHealth();
  const [confirm, setConfirm] = useState<null | "approve" | "reject">(null);
  const [view, setView] = useState<"raw" | "rendered">("raw");
  const approve = useDecideAction("approve");
  const reject = useDecideAction("reject");
  const hashState = useHashCheck(action.id, action.payload, action.payload_sha256);
  const Icon = KIND_ICON[action.kind as keyof typeof KIND_ICON] ?? FileText;
  const pending = action.status === "pending";
  const readOnlyBlocked = health?.gate_mode === "read_only" && action.kind !== "reply_send";
  const hash = normalizeHex(action.payload_sha256);
  const canonical = /canonical/i.test(action.what_else_happens);
  const error = approve.error ?? reject.error;
  const renderable = action.kind !== "reply_send";

  return (
    <article id={action.id} className={cx("card scroll-mt-24 overflow-hidden", hashState === "mismatch" && "border-critical-line")} aria-labelledby={`${action.id}-title`}>
      <header className="flex flex-wrap items-center gap-2 border-b border-line bg-canvas px-3 py-2">
        <Badge tone="info">
          <Icon aria-hidden className="h-3.5 w-3.5" />
          <span id={`${action.id}-title`}>{ACTION_KIND[action.kind] ?? action.kind}</span>
        </Badge>
        <IdChip value={action.id} display={action.id.length > 20 ? `${action.id.slice(0, 20)}…` : action.id} />
        <StatusPill status={action.status} label={ACTION_STATUS[action.status]} />
        <span className="ml-auto text-body-sm text-ink-muted">
          Source:{" "}
          {action.subject_type === "ticket" ? (
            <Link to={`/queue/${encodeURIComponent(action.subject_id)}`} className="font-mono text-label-md text-primary hover:underline">
              ticket {action.subject_id.length > 22 ? `${action.subject_id.slice(0, 22)}…` : action.subject_id}
            </Link>
          ) : (
            <Link to="/pulse" className="text-primary hover:underline">
              {SUBJECT[action.subject_type] ?? action.subject_type} (weekly run)
            </Link>
          )}
        </span>
      </header>

      <div className="space-y-3 p-3">
        <div className="rounded border border-line px-3 py-2">
          <p className="eyebrow">Destination</p>
          <p className="mt-0.5 text-body-lg text-ink">{action.destination || "Not specified"}</p>
          <TargetDetails target={action.target} />
        </div>

        {action.kind === "reply_send" ? <ReplacementNote ticketId={action.subject_id} draftVersionId={String(action.target.draft_version_id ?? "")} /> : null}

        <div className="overflow-hidden rounded border border-line">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-canvas px-3 py-1.5">
            <span className="eyebrow">Exact payload</span>
            <span className="flex items-center gap-1.5 text-body-sm text-ink-muted">
              Payload hash (sha256) <IdChip value={hash} display={`${hash.slice(0, 10)}…${hash.slice(-6)}`} />
            </span>
            <HashBadge state={hashState} />
            {renderable ? (
              <div role="group" aria-label="Payload view" className="ml-auto flex rounded border border-line bg-surface p-0.5">
                {(["raw", "rendered"] as const).map((v) => (
                  <button
                    key={v}
                    type="button"
                    aria-pressed={view === v}
                    onClick={() => setView(v)}
                    className={cx("rounded px-2 py-0.5 text-body-sm capitalize", view === v ? "bg-primary-wash text-primary-hover" : "text-ink-muted")}
                  >
                    {v}
                  </button>
                ))}
              </div>
            ) : null}
          </div>
          {view === "raw" || !renderable ? (
            <pre tabIndex={0} aria-label="Exact payload" className="max-h-80 overflow-auto whitespace-pre-wrap break-words bg-surface p-3 font-mono text-label-md text-ink">{action.payload}</pre>
          ) : (
            <div tabIndex={0} aria-label="Rendered payload" className="max-h-80 overflow-auto p-4">
              <PlainMarkdown source={action.payload} />
            </div>
          )}
        </div>

        <Banner tone={canonical ? "warning" : "info"} title="What else happens">
          {action.what_else_happens || "Nothing else. This action only."}
        </Banner>

        {hashState === "mismatch" ? (
          <Banner tone="critical" title="Payload does not match its frozen hash">
            The text shown is not the text that was proposed. Approval is disabled for this action.
          </Banner>
        ) : null}
        {readOnlyBlocked && pending ? <Banner tone="warning">The MCP writer is unavailable, so document and email approvals are paused.</Banner> : null}
        {error ? (
          <Banner tone="critical">
            {error instanceof ApiError && error.status === 409 ? `${error.message} The list has been refreshed.` : error instanceof Error ? error.message : "The request failed."}
          </Banner>
        ) : null}
        {action.decided_at ? (
          <p className="text-body-sm text-ink-muted">
            Decided by {action.decided_by ?? "—"} at {formatDateTime(action.decided_at)}
            {action.executed_at ? ` · written at ${formatDateTime(action.executed_at)}` : ""}
          </p>
        ) : null}
      </div>

      {pending ? (
        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-line bg-canvas px-3 py-2">
          <span className="mr-auto flex items-center gap-1.5 text-body-sm text-ink-faint">
            <ShieldCheck aria-hidden className="h-4 w-4" /> Single approval for this action only
          </span>
          <Button variant="destructive" icon={X} size="sm" onClick={() => setConfirm("reject")} disabled={approve.isPending}>
            Reject
          </Button>
          <Button
            variant="primary"
            icon={CheckCircle2}
            size="sm"
            onClick={() => setConfirm("approve")}
            disabled={hashState !== "match" || readOnlyBlocked || reject.isPending}
            title={hashState === "checking" ? "Checking payload hash…" : undefined}
          >
            Approve
          </Button>
        </footer>
      ) : null}

      <ConfirmDialog
        open={confirm === "approve"}
        onOpenChange={(open) => (open ? undefined : setConfirm(null))}
        title="Approve this action?"
        confirmLabel={action.kind === "reply_send" ? "Approve and send" : "Approve and write"}
        busy={approve.isPending}
        onConfirm={() => approve.mutate(action.id, { onSettled: () => setConfirm(null) })}
      >
        <p>
          This will write to <strong className="text-ink">{action.destination || "the configured destination"}</strong>, once.
        </p>
        <p className="flex flex-wrap items-center gap-1.5 text-body-sm">
          Payload hash <IdChip value={hash} display={`${hash.slice(0, 12)}…`} />
        </p>
        <p>{action.what_else_happens || "Nothing else happens."}</p>
      </ConfirmDialog>
      <ConfirmDialog
        open={confirm === "reject"}
        onOpenChange={(open) => (open ? undefined : setConfirm(null))}
        title="Reject this action?"
        confirmLabel="Reject"
        tone="destructive"
        busy={reject.isPending}
        onConfirm={() => reject.mutate(action.id, { onSettled: () => setConfirm(null) })}
      >
        <p>Nothing is written. Other actions are not affected.</p>
      </ConfirmDialog>
    </article>
  );
}

function HashBadge({ state }: { state: HashState }) {
  if (state === "checking") return <Badge>checking…</Badge>;
  if (state === "match")
    return (
      <Badge tone="success" title="The payload shown hashes to the frozen sha256">
        <ShieldCheck aria-hidden className="h-3 w-3" /> matches
      </Badge>
    );
  return (
    <Badge tone="critical">
      <ShieldAlert aria-hidden className="h-3 w-3" /> mismatch
    </Badge>
  );
}

function TargetDetails({ target }: { target: Record<string, unknown> }) {
  const entries = Object.entries(target).filter(([, v]) => v !== null && v !== undefined && v !== "");
  if (entries.length === 0) return null;
  return (
    <dl className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1">
      {entries.map(([k, v]) => (
        <div key={k} className="flex items-baseline gap-1.5">
          <dt className="text-label-sm uppercase text-ink-faint">{k.replace(/_/g, " ")}</dt>
          <dd className="font-mono text-label-md text-ink-muted">{Array.isArray(v) ? (v.length ? v.join(", ") : "(none)") : String(v)}</dd>
        </div>
      ))}
    </dl>
  );
}

/** For reply_send: shows when this reply replaces a draft that failed review (arch §8.6, G9). */
function ReplacementNote({ ticketId, draftVersionId }: { ticketId: string; draftVersionId: string }) {
  const { data } = useTicket(ticketId);
  if (!data) return null;
  const failed = data.drafts.filter((d) => d.id !== draftVersionId && ((d.reviewer_result as ReviewerResult).failures ?? []).length > 0);
  if (failed.length === 0) return null;
  const flags = [...new Set(failed.flatMap((d) => (d.reviewer_result as ReviewerResult).failures ?? []))];
  return (
    <Banner tone="critical" title="This reply replaces a draft that failed review">
      <span className="flex flex-wrap items-center gap-1.5">
        Original flags:
        {flags.map((f) => (
          <Badge key={f} tone="critical" mono>
            {f}
          </Badge>
        ))}
      </span>
    </Banner>
  );
}
