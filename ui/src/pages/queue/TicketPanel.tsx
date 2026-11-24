import * as Collapsible from "@radix-ui/react-collapsible";
import { ChevronDown, Lock, ShieldX, X } from "lucide-react";
import { useState } from "react";

import type { AgentOutput, FactsSearched, ReviewerResult, RoutingNote, TicketDetail } from "../../api/types";
import { CitationLink } from "../../components/CitationLink";
import { LastChecked } from "../../components/LastChecked";
import { RedactedText } from "../../components/RedactedText";
import { ReviewerChecklist } from "../../components/ReviewerChecklist";
import { AuditCard, Badge, Banner, Button, Counter, IdChip, KeyValue, PriorityPill, ProgressBar, StatusPill, WindowLabel } from "../../components/ui";
import { CAPS, NO_CITATION, TICKET_STATUS } from "../../copy";
import { cx } from "../../lib/cx";
import { countSentences, formatDate, formatDateTime, formatWindow } from "../../lib/format";
import { TicketActions } from "./TicketActions";

function latest(outputs: AgentOutput[], agent: string): AgentOutput | undefined {
  return outputs.filter((o) => o.agent === agent).sort((a, b) => b.attempt - a.attempt || b.id - a.id)[0];
}

const ORIGIN_LABEL: Record<string, string> = {
  orchestrator: "agent draft",
  canonical: "canonical explainer",
  human_edit: "human edit",
  rework: "reviewer rework",
  refinement: "refinement",
};

export function TicketPanel({ ticket, onClose }: { ticket: TicketDetail; onClose?: () => void }) {
  const draft = ticket.current_draft;
  const review = (draft?.reviewer_result ?? {}) as ReviewerResult;
  const routing = (draft?.routing_note ?? {}) as RoutingNote;
  const facts = latest(ticket.agent_outputs, "facts");
  const factsOut = (facts?.written_output ?? {}) as { covered?: boolean; searched?: FactsSearched; snapshot?: string };
  const blocked = ticket.status === "blocked";
  const sentences = draft ? countSentences(draft.reply_text) : 0;
  const notCovered = Boolean(draft && !draft.citation_url && factsOut.covered === false);
  const evidence = routing.evidence;

  return (
    <article aria-labelledby="ticket-title" className="flex flex-col gap-3">
      <div className="card p-3">
        <div className="flex items-start justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5">
            <h2 id="ticket-title" className="sr-only">
              Ticket {ticket.id}
            </h2>
            <IdChip value={ticket.id} display={ticket.id.length > 18 ? `${ticket.id.slice(0, 18)}…` : ticket.id} />
            <StatusPill status={ticket.status} label={TICKET_STATUS[ticket.status]} />
            <PriorityPill priority={ticket.priority} />
          </div>
          {onClose ? (
            <Button variant="ghost" size="sm" aria-label="Close ticket" onClick={onClose}>
              <X aria-hidden className="h-4 w-4" />
            </Button>
          ) : null}
        </div>
        <p className="mt-1.5 text-body-sm text-ink-muted">
          {ticket.route_team ?? "Unrouted"} · received <time dateTime={ticket.received_at}>{formatDateTime(ticket.received_at)}</time> · {ticket.channel}
        </p>
        <div className="mt-3 rounded border border-line bg-canvas p-2.5">
          <p className="eyebrow mb-1">Customer question (redacted)</p>
          <p className="text-body-lg font-normal text-ink">
            <RedactedText text={ticket.question_redacted} />
          </p>
          {ticket.pii_types_found.length > 0 ? (
            <p className="mt-1.5 text-body-sm text-ink-faint">Removed before storage: {ticket.pii_types_found.join(", ")}</p>
          ) : null}
        </div>
      </div>

      {blocked ? (
        <Banner tone="critical" title="Blocked by reviewer after 1 rework round. This draft cannot be sent.">
          Edit the draft to re-run the reviewer. A passing edit creates a new reply for approval.
        </Banner>
      ) : null}
      {draft?.disagreement ? <Banner tone="warning" title="Escalated, ticket stays open">{draft.disagreement}</Banner> : null}

      {draft ? (
        <AuditCard
          title={blocked ? "Draft reply (blocked)" : "Draft reply"}
          aside={<Counter value={sentences} cap={CAPS.sentences} unit="sentences" />}
          className={cx(blocked && "border-critical-line")}
        >
          <p className={cx("whitespace-pre-wrap text-body-lg font-normal", blocked ? "text-critical-fg" : "text-ink")}>
            <RedactedText text={draft.reply_text} />
          </p>
          <div className="mt-3 flex flex-col gap-1.5">
            {draft.citation_url ? <CitationLink url={draft.citation_url} /> : <Badge tone="neutral">{NO_CITATION}</Badge>}
            <LastChecked date={draft.last_checked} snapshot={draft.origin === "human_edit" ? null : factsOut.snapshot} />
            <div className="flex flex-wrap gap-1.5">
              <Badge tone={draft.origin === "canonical" ? "primary" : "neutral"}>origin: {ORIGIN_LABEL[draft.origin] ?? draft.origin}</Badge>
              <Badge tone="neutral" mono>
                v{draft.version}
              </Badge>
            </div>
          </div>
          {notCovered && factsOut.searched ? <SearchedList searched={factsOut.searched} /> : null}
          {routing.multi_scheme && routing.multi_scheme.length > 1 ? (
            <p className="mt-2 text-body-sm text-ink-muted">
              Several schemes named: {routing.multi_scheme.join(", ")}. The draft answers one; edit or refine with “Different scheme” to change it.
            </p>
          ) : null}
        </AuditCard>
      ) : (
        <Banner tone="info">No draft yet. The agents are still working on this ticket.</Banner>
      )}

      {draft ? (
        <section className="card border-info-line bg-info-bg p-3">
          <h3 className="eyebrow mb-2 flex items-center gap-1.5">
            <Lock aria-hidden className="h-3.5 w-3.5" /> Routing note · internal, never sent
          </h3>
          <dl className="grid grid-cols-2 gap-2">
            <KeyValue label="Team">{routing.team ?? ticket.route_team ?? "—"}</KeyValue>
            <KeyValue label="Priority">{routing.priority ?? ticket.priority ?? "—"}</KeyValue>
          </dl>
          {routing.why ? <p className="mt-2 text-body-md text-ink">{routing.why}</p> : null}
          {evidence?.review_window || evidence?.ticket_window ? (
            <div className="mt-2 grid grid-cols-1 gap-2 sm:grid-cols-2">
              {evidence.review_window ? <WindowLabel kind="review" value={formatWindow(...evidence.review_window)} /> : null}
              {evidence.ticket_window ? <WindowLabel kind="ticket" value={formatWindow(...evidence.ticket_window)} /> : null}
            </div>
          ) : null}
          {evidence?.review_ids_sample?.length ? (
            <div className="mt-2 flex flex-wrap items-center gap-1">
              <span className="text-body-sm text-ink-muted">Sample reviews:</span>
              {evidence.review_ids_sample.map((rid) => (
                <IdChip key={rid} value={rid} />
              ))}
            </div>
          ) : null}
        </section>
      ) : null}

      {draft ? (
        <AuditCard title="Reviewer checks">
          <ReviewerChecklist result={review} />
        </AuditCard>
      ) : null}

      <AgentOutputs outputs={ticket.agent_outputs} />
      <History ticket={ticket} />

      <TicketActions ticket={ticket} />
    </article>
  );
}

function SearchedList({ searched }: { searched: FactsSearched }) {
  return (
    <Collapsible.Root className="mt-3 rounded border border-line">
      <Collapsible.Trigger className="group flex w-full items-center justify-between px-2.5 py-1.5 text-body-sm font-medium text-ink-muted hover:bg-canvas">
        Searched: {searched.schemes?.length ?? 0} schemes · {searched.sections?.length ?? 0} sections
        <ChevronDown aria-hidden className="h-4 w-4 transition-transform group-data-[state=open]:rotate-180" />
      </Collapsible.Trigger>
      <Collapsible.Content className="space-y-1.5 border-t border-line px-2.5 py-2">
        <div className="flex flex-wrap gap-1">
          {(searched.schemes ?? []).map((s) => (
            <Badge key={s} mono>
              {s}
            </Badge>
          ))}
        </div>
        <div className="flex flex-wrap gap-1">
          {(searched.sections ?? []).map((s) => (
            <Badge key={s}>{s}</Badge>
          ))}
        </div>
      </Collapsible.Content>
    </Collapsible.Root>
  );
}

function AgentOutputs({ outputs }: { outputs: AgentOutput[] }) {
  const [open, setOpen] = useState(false);
  const agents = ["facts", "pattern", "compliance"].map((a) => latest(outputs, a)).filter((a): a is AgentOutput => Boolean(a));
  if (agents.length === 0) return null;
  return (
    <Collapsible.Root open={open} onOpenChange={setOpen} className="card">
      <Collapsible.Trigger className="group flex w-full items-center justify-between px-3 py-2 text-left">
        <span className="eyebrow">Agent outputs</span>
        <ChevronDown aria-hidden className="h-4 w-4 text-ink-faint transition-transform group-data-[state=open]:rotate-180" />
      </Collapsible.Trigger>
      <Collapsible.Content className="grid grid-cols-1 gap-2 border-t border-line p-3 sm:grid-cols-3">
        {agents.map((a) => {
          const errored = a.model_raw_choice === "ERROR" || a.model_raw_choice === null;
          return (
            <div key={a.id} className="rounded border border-line bg-canvas p-2.5">
              <p className="text-body-md font-semibold capitalize text-ink">{a.agent}</p>
              <dl className="mt-1.5 space-y-1.5">
                <KeyValue label="Model picked">
                  <span className={cx("font-mono text-label-md", errored && "text-warning-fg")}>{a.model_raw_choice ?? "—"}</span>
                </KeyValue>
                <KeyValue label="Validated">
                  <span className="font-mono text-label-md text-success-fg">{a.validated_choice ?? "—"}</span>
                </KeyValue>
              </dl>
              {errored ? <p className="mt-1.5 text-body-sm text-ink-faint">No model pick; code rules decided.</p> : null}
            </div>
          );
        })}
      </Collapsible.Content>
    </Collapsible.Root>
  );
}

function History({ ticket }: { ticket: TicketDetail }) {
  const versions = [...ticket.drafts].sort((a, b) => a.version - b.version);
  return (
    <AuditCard title="History" aside={<span className="font-mono text-label-sm text-ink-muted">Refinements {ticket.refinement_count} / {CAPS.refinements}</span>}>
      <ProgressBar value={ticket.refinement_count} max={CAPS.refinements} label={`Refinements used: ${ticket.refinement_count} of ${CAPS.refinements}`} />
      <ol className="mt-3 flex flex-wrap items-center gap-1.5">
        {versions.map((v) => {
          const result = v.reviewer_result as ReviewerResult;
          const failed = (result.failures ?? []).length > 0;
          return (
            <li key={v.id}>
              <Badge tone={failed ? "critical" : v.is_current ? "primary" : "neutral"} mono title={failed ? `Failed: ${(result.failures ?? []).join(", ")}` : undefined}>
                v{v.version} {ORIGIN_LABEL[v.origin] ?? v.origin}
                {v.is_current ? " · current" : ""}
                {failed ? " · failed" : ""}
              </Badge>
            </li>
          );
        })}
      </ol>
      {ticket.refinements.length > 0 ? (
        <ol className="mt-3 space-y-1 border-t border-line pt-2">
          {ticket.refinements.map((r) => (
            <li key={r.id} className="flex flex-wrap items-center gap-2 text-body-sm text-ink-muted">
              <span className="font-mono text-label-sm text-ink">#{r.n}</span>
              <span>{r.kind.replace(/_/g, " ")}</span>
              <span className="font-mono text-label-sm">{r.nodes_rerun.join(" → ")}</span>
              {r.hint_redacted ? <span className="italic">“{r.hint_redacted}”</span> : null}
              <time className="ml-auto font-mono text-label-sm" dateTime={r.created_at}>
                {formatDate(r.created_at)}
              </time>
            </li>
          ))}
        </ol>
      ) : null}
      {ticket.status === "blocked" && versions.some((v) => (v.reviewer_result as ReviewerResult).failures?.length) ? (
        <p className="mt-2 flex items-center gap-1.5 text-body-sm text-critical-fg">
          <ShieldX aria-hidden className="h-4 w-4" /> No reply-send action exists for a blocked version.
        </p>
      ) : null}
    </AuditCard>
  );
}
