import * as Dialog from "@radix-ui/react-dialog";
import * as DropdownMenu from "@radix-ui/react-dropdown-menu";
import * as Tooltip from "@radix-ui/react-tooltip";
import { useQuery } from "@tanstack/react-query";
import { Check, ChevronDown, Pencil, SlidersHorizontal, X } from "lucide-react";
import { useState } from "react";

import { ApiError, api } from "../../api/client";
import { useDecideAction, useEditTicket, useRefineTicket, useRejectTicket } from "../../api/hooks";
import type { RefineKind, TicketDetail } from "../../api/types";
import { ConfirmDialog } from "../../components/ConfirmDialog";
import { Banner, Button, IdChip } from "../../components/ui";
import { CAPS, REFINE_KINDS } from "../../copy";
import { normalizeHex } from "../../lib/safe";

function errorText(error: unknown): string | null {
  if (!error) return null;
  if (error instanceof ApiError) {
    if (error.code === "refinement_cap_reached") return `Refinement limit reached (${CAPS.refinements}).`;
    return error.message;
  }
  return "The request failed.";
}

/**
 * Human actions for one ticket (arch §8.7). "Approve & send" is not rendered at all
 * for a blocked ticket or when no pending reply_send exists (plan P9.6, eval).
 */
export function TicketActions({ ticket }: { ticket: TicketDetail }) {
  const [dialog, setDialog] = useState<null | "send" | "edit" | "reject" | RefineKind>(null);
  const close = () => setDialog(null);
  const blocked = ticket.status === "blocked";
  const finished = ticket.status === "sent" || ticket.status === "rejected";
  const canSend = !blocked && !finished && Boolean(ticket.pending_reply_send_id);
  const capReached = ticket.refinement_count >= CAPS.refinements;

  const approve = useDecideAction("approve");
  const edit = useEditTicket();
  const reject = useRejectTicket();
  const refine = useRefineTicket();
  const error = errorText(approve.error ?? edit.error ?? reject.error ?? refine.error);

  if (finished) {
    return (
      <div className="card px-3 py-2.5 text-body-sm text-ink-muted">
        {ticket.status === "sent" ? "Reply written to the outbox." : "Draft rejected."} No further actions on this ticket.
      </div>
    );
  }

  return (
    <div className="sticky bottom-0 z-10 -mx-px rounded-md border border-line bg-surface/95 p-3 shadow-2 backdrop-blur">
      {error ? (
        <Banner tone="critical" className="mb-2">
          {error}
        </Banner>
      ) : null}
      {blocked ? <p className="mb-2 text-body-sm text-ink-muted">Edit the draft to re-run the reviewer.</p> : null}
      <div className="flex flex-wrap items-center gap-2">
        {canSend ? (
          <Button variant="primary" icon={Check} onClick={() => setDialog("send")} data-testid="approve-send">
            Approve &amp; send
          </Button>
        ) : null}
        <Button variant={blocked ? "primary" : "secondary"} icon={Pencil} onClick={() => setDialog("edit")} disabled={!ticket.current_draft}>
          Edit
        </Button>
        <Button variant="destructive" icon={X} onClick={() => setDialog("reject")}>
          Reject
        </Button>
        <RefineMenu disabled={capReached} busy={refine.isPending} onPick={(kind) => setDialog(kind)} />
      </div>

      {dialog === "send" && ticket.pending_reply_send_id ? (
        <SendDialog
          actionId={ticket.pending_reply_send_id}
          busy={approve.isPending}
          onCancel={close}
          onConfirm={() => approve.mutate(ticket.pending_reply_send_id ?? "", { onSettled: close })}
        />
      ) : null}

      {dialog === "edit" && ticket.current_draft ? (
        <EditDialog
          initial={ticket.current_draft.reply_text}
          busy={edit.isPending}
          onCancel={close}
          onSave={(text) => edit.mutate({ id: ticket.id, arg: text }, { onSuccess: close })}
        />
      ) : null}

      <ConfirmDialog
        open={dialog === "reject"}
        onOpenChange={(open) => (open ? undefined : close())}
        title="Reject this draft?"
        confirmLabel="Reject draft"
        tone="destructive"
        busy={reject.isPending}
        onConfirm={() => reject.mutate({ id: ticket.id, arg: undefined }, { onSettled: close })}
      >
        <p>The ticket is marked rejected and its pending reply is cancelled. No other ticket, action or explainer changes.</p>
      </ConfirmDialog>

      {dialog && REFINE_KINDS.some((r) => r.kind === dialog) ? (
        <RefineDialog
          kind={dialog as RefineKind}
          used={ticket.refinement_count}
          busy={refine.isPending}
          onCancel={close}
          onConfirm={(hint) => refine.mutate({ id: ticket.id, arg: { kind: dialog as RefineKind, hint } }, { onSettled: close })}
        />
      ) : null}
    </div>
  );
}

function RefineMenu({ disabled, busy, onPick }: { disabled: boolean; busy: boolean; onPick: (kind: RefineKind) => void }) {
  const trigger = (
    <Button icon={SlidersHorizontal} disabled={disabled} busy={busy} aria-label={disabled ? `Refine (limit of ${CAPS.refinements} reached)` : "Refine"}>
      Refine
      <ChevronDown aria-hidden className="h-3.5 w-3.5" />
    </Button>
  );
  if (disabled) {
    return (
      <Tooltip.Provider delayDuration={150}>
        <Tooltip.Root>
          <Tooltip.Trigger asChild>
            <span tabIndex={0} className="inline-flex rounded">
              {trigger}
            </span>
          </Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content sideOffset={6} className="z-50 rounded bg-ink px-2 py-1 text-body-sm text-white shadow-2">
              Refinement limit reached ({CAPS.refinements})
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      </Tooltip.Provider>
    );
  }
  return (
    <DropdownMenu.Root>
      <DropdownMenu.Trigger asChild>{trigger}</DropdownMenu.Trigger>
      <DropdownMenu.Portal>
        <DropdownMenu.Content align="end" sideOffset={6} className="z-50 min-w-[12rem] rounded-md border border-line bg-surface p-1 shadow-2">
          {REFINE_KINDS.map(({ kind, label }) => (
            <DropdownMenu.Item
              key={kind}
              onSelect={() => onPick(kind)}
              className="cursor-pointer rounded px-2.5 py-1.5 text-body-md text-ink outline-none data-[highlighted]:bg-primary-wash"
            >
              {label}
            </DropdownMenu.Item>
          ))}
        </DropdownMenu.Content>
      </DropdownMenu.Portal>
    </DropdownMenu.Root>
  );
}

function SendDialog({ actionId, busy, onCancel, onConfirm }: { actionId: string; busy: boolean; onCancel: () => void; onConfirm: () => void }) {
  const { data: action } = useQuery({ queryKey: ["action", actionId], queryFn: () => api.action(actionId) });
  return (
    <ConfirmDialog
      open
      onOpenChange={(open) => (open ? undefined : onCancel())}
      title="Approve and send this reply?"
      confirmLabel="Approve and send"
      busy={busy}
      onConfirm={onConfirm}
    >
      <p>
        This writes the reply below to <strong className="text-ink">{action?.destination || "the outbox"}</strong>. Nothing else happens.
      </p>
      {action ? (
        <>
          <pre tabIndex={0} aria-label="Exact payload" className="max-h-40 overflow-auto whitespace-pre-wrap rounded border border-line bg-canvas p-2 font-mono text-label-md text-ink">{action.payload}</pre>
          <p className="flex flex-wrap items-center gap-1.5 text-body-sm">
            Payload hash (sha256) <IdChip value={normalizeHex(action.payload_sha256)} display={`${normalizeHex(action.payload_sha256).slice(0, 12)}…`} />
          </p>
        </>
      ) : (
        <p className="text-body-sm text-ink-faint">Loading the exact payload…</p>
      )}
    </ConfirmDialog>
  );
}

function EditDialog({ initial, busy, onCancel, onSave }: { initial: string; busy: boolean; onCancel: () => void; onSave: (text: string) => void }) {
  const [text, setText] = useState(initial);
  const unchanged = text.trim() === initial.trim();
  return (
    <Dialog.Root open onOpenChange={(open) => (open ? undefined : onCancel())}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-50 bg-ink/30" />
        <Dialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100vw-2rem)] max-w-xl -translate-x-1/2 -translate-y-1/2 rounded-md border border-line bg-surface p-5 shadow-2">
          <Dialog.Title className="text-headline-sm">Edit draft reply</Dialog.Title>
          <Dialog.Description className="mt-1 text-body-md text-ink-muted">
            Saving creates a new version and re-runs the reviewer. Personal details are redacted before storage. Max {CAPS.sentences} sentences.
          </Dialog.Description>
          <label htmlFor="edit-draft" className="sr-only">
            Draft reply
          </label>
          <textarea
            id="edit-draft"
            value={text}
            onChange={(event) => setText(event.target.value)}
            rows={6}
            className="mt-3 w-full rounded border border-line-strong p-2.5 text-body-lg font-normal focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
          <div className="mt-4 flex items-center justify-end gap-2">
            {unchanged ? <span className="mr-auto text-body-sm text-ink-faint">No changes yet.</span> : null}
            <Dialog.Close asChild>
              <Button disabled={busy}>Cancel</Button>
            </Dialog.Close>
            <Button variant="primary" busy={busy} disabled={unchanged || !text.trim()} onClick={() => onSave(text)}>
              Save and re-review
            </Button>
          </div>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}

const REFINE_HELP: Record<RefineKind, string> = {
  shorter: "Reduces the reply to its primary line and keeps the citation.",
  different_scheme: "Re-runs the Facts agent. Name the scheme the customer meant.",
  check_fee_issue: "Re-runs the Pattern agent against the fees theme.",
  other: "Re-runs the orchestrator and reviewer with your note.",
};

function RefineDialog({
  kind,
  used,
  busy,
  onCancel,
  onConfirm,
}: {
  kind: RefineKind;
  used: number;
  busy: boolean;
  onCancel: () => void;
  onConfirm: (hint: string) => void;
}) {
  const [hint, setHint] = useState("");
  const label = REFINE_KINDS.find((r) => r.kind === kind)?.label.replace("…", "") ?? kind;
  return (
    <ConfirmDialog
      open
      onOpenChange={(open) => (open ? undefined : onCancel())}
      title={`Refine: ${label}`}
      confirmLabel={`Refine (${used + 1} of ${CAPS.refinements})`}
      busy={busy}
      onConfirm={() => onConfirm(hint)}
    >
      <p>{REFINE_HELP[kind]}</p>
      {kind === "shorter" ? null : (
        <div>
          <label htmlFor="refine-hint" className="block text-label-lg text-ink-muted">
            Hint (optional, redacted before use)
          </label>
          <input
            id="refine-hint"
            value={hint}
            onChange={(event) => setHint(event.target.value)}
            className="mt-1 h-9 w-full rounded border border-line-strong px-2.5 text-body-md text-ink focus:border-primary focus:outline-none focus:ring-2 focus:ring-primary/20"
          />
        </div>
      )}
    </ConfirmDialog>
  );
}
