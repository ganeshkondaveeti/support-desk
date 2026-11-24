import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { AppShell } from "../components/AppShell";
import { DISCLAIMER } from "../copy";
import { mockDb } from "../mocks/handlers";
import { renderWithApp } from "../test/render";
import ApprovalsPage from "./approvals/ApprovalsPage";
import ExplainersPage from "./explainers/ExplainersPage";
import PulsePage from "./pulse/PulsePage";
import QueuePage from "./queue/QueuePage";
import SourcesPage from "./sources/SourcesPage";

const BLOCKED = "seed_oos_5";
const OPEN = "seed_adv_7";

describe("AppShell", () => {
  it("shows the disclaimer, last-checked dates and pending count", async () => {
    renderWithApp(
      <AppShell>
        <p>child</p>
      </AppShell>,
    );
    expect(screen.getAllByText(DISCLAIMER).length).toBeGreaterThan(0);
    expect(await screen.findByText(/Corpus last checked: A 28-Jun-2026 · B 01-Sep-2026/)).toBeInTheDocument();
    expect(screen.getByLabelText(`Approvals, ${mockDb().health.pending_approvals} pending`)).toBeInTheDocument();
  });

  it("shows the read-only gate banner", async () => {
    mockDb().health.gate_mode = "read_only";
    renderWithApp(
      <AppShell>
        <p>child</p>
      </AppShell>,
    );
    expect(await screen.findByText(/Gate is read-only/)).toBeInTheDocument();
  });
});

describe("Queue", () => {
  it("renders no Send button at all for a blocked ticket", async () => {
    renderWithApp(<QueuePage />, { route: `/queue/${BLOCKED}`, path: "/queue/:ticketId" });
    expect(await screen.findByText(/Blocked by reviewer after 1 rework round/)).toBeInTheDocument();
    expect(screen.queryByTestId("approve-send")).toBeNull();
    expect(screen.queryByRole("button", { name: /Approve & send/ })).toBeNull();
    expect(screen.getByRole("button", { name: "Edit" })).toBeEnabled();
    expect(screen.getByText("UNCITED_CLAIM")).toBeInTheDocument();
  });

  it("offers Approve & send for a passing draft, with citation and routing note", async () => {
    renderWithApp(<QueuePage />, { route: `/queue/${OPEN}`, path: "/queue/:ticketId" });
    expect(await screen.findByTestId("approve-send")).toBeInTheDocument();
    expect(screen.getByText(/internal, never sent/i)).toBeInTheDocument();
    expect(screen.getByText("Review window")).toBeInTheDocument();
    expect(screen.getByText("Ticket window")).toBeInTheDocument();
    await waitFor(() => expect(screen.getAllByRole("link", { name: /groww\.in/ }).length).toBeGreaterThan(0));
  });

  it("the send confirmation names the destination and shows the exact payload", async () => {
    const user = userEvent.setup();
    renderWithApp(<QueuePage />, { route: `/queue/${OPEN}`, path: "/queue/:ticketId" });
    await user.click(await screen.findByTestId("approve-send"));
    const dialog = await screen.findByRole("dialog");
    expect(await within(dialog).findByText(/Outbox · ticket seed_adv_7/)).toBeInTheDocument();
    expect(within(dialog).getByText(/Exit load of 1%, if redeemed within 15 days/)).toBeInTheDocument();
  });

  it("disables Refine at 5 of 5", async () => {
    const detail = mockDb().ticketDetails[OPEN];
    if (detail) detail.refinement_count = 5;
    renderWithApp(<QueuePage />, { route: `/queue/${OPEN}`, path: "/queue/:ticketId" });
    expect(await screen.findByRole("button", { name: /Refine \(limit of 5 reached\)/ })).toBeDisabled();
  });

  it("filters by status through the URL", async () => {
    const user = userEvent.setup();
    renderWithApp(<QueuePage />, { route: "/queue", path: "/queue" });
    await screen.findAllByText("Awaiting review");
    await user.click(screen.getByRole("button", { name: /^Blocked/ }));
    await waitFor(() => expect(screen.queryAllByRole("row").slice(1).every((r) => within(r).queryByText("Blocked"))).toBe(true));
  });
});

describe("Approvals", () => {
  it("has no bulk approve", async () => {
    renderWithApp(<ApprovalsPage />, { route: "/approvals", path: "/approvals" });
    await screen.findAllByRole("article");
    expect(screen.queryByRole("checkbox")).toBeNull();
    expect(screen.queryByText(/approve all/i)).toBeNull();
  });

  it("approving one action changes only that card", async () => {
    const user = userEvent.setup();
    renderWithApp(<ApprovalsPage />, { route: "/approvals", path: "/approvals" });
    const cards = await screen.findAllByRole("article");
    const before = cards.length;
    const first = cards[0];
    if (!first) throw new Error("no cards");
    const id = first.id;
    const approve = within(first).getByRole("button", { name: "Approve" });
    await waitFor(() => expect(approve).toBeEnabled()); // waits for the hash check
    await user.click(approve);
    const dialog = await screen.findByRole("dialog");
    expect(within(dialog).getByText(/This will write to/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: /Approve and (write|send)/ }));
    await waitFor(() => expect(screen.getAllByRole("article")).toHaveLength(before - 1));
    expect(document.getElementById(id)).toBeNull();
    const statuses = mockDb().actions.items.filter((a) => a.status !== "pending" && a.status !== "executed");
    expect(mockDb().actions.items.find((a) => a.id === id)?.status).toBe("executed");
    expect(statuses.every((a) => a.id !== id)).toBe(true);
  });

  it("disables Approve when the payload doesn't match its hash", async () => {
    const first = mockDb().actions.items.find((a) => a.status === "pending");
    if (!first) throw new Error("no pending action");
    first.payload = `${first.payload} tampered`;
    renderWithApp(<ApprovalsPage />, { route: "/approvals", path: "/approvals" });
    await screen.findAllByRole("article");
    expect(await screen.findByText("Payload does not match its frozen hash")).toBeInTheDocument();
    const card = document.getElementById(first.id);
    if (!card) throw new Error("card missing");
    expect(within(card).getByRole("button", { name: "Approve" })).toBeDisabled();
  });

  it("pauses doc and email approvals in read-only gate mode, not replies", async () => {
    mockDb().health.gate_mode = "read_only";
    renderWithApp(<ApprovalsPage />, { route: "/approvals", path: "/approvals" });
    const cards = await screen.findAllByRole("article");
    await waitFor(() => expect(screen.getAllByText(/document and email approvals are paused/).length).toBeGreaterThan(0));
    const byKind = (kind: string) => mockDb().actions.items.find((a) => a.status === "pending" && a.kind === kind);
    const doc = cards.find((c) => c.id === byKind("doc_append")?.id);
    const reply = cards.find((c) => c.id === byKind("reply_send")?.id);
    if (!doc || !reply) throw new Error("fixture missing kinds");
    await waitFor(() => expect(within(reply).getByRole("button", { name: "Approve" })).toBeEnabled());
    expect(within(doc).getByRole("button", { name: "Approve" })).toBeDisabled();
  });
});

describe("Other views", () => {
  it("pulse shows caps and the run's actions", async () => {
    renderWithApp(<PulsePage />, { route: "/pulse", path: "/pulse" });
    expect(await screen.findByText("Pulse preview")).toBeInTheDocument();
    expect(screen.getByLabelText(/of 250 words/)).toBeInTheDocument();
    expect(screen.getByLabelText(/of 6 bullets/)).toBeInTheDocument();
    expect(await screen.findByText(/This run proposed 4 actions/)).toBeInTheDocument();
  });

  it("pulse reports the weekly run lock", async () => {
    const user = userEvent.setup();
    renderWithApp(<PulsePage />, { route: "/pulse", path: "/pulse" });
    await user.click(await screen.findByRole("button", { name: "Run weekly loop now" }));
    expect(await screen.findByText(/Run already in progress/)).toBeInTheDocument();
  });

  it("sources lists uncovered sections and the drift tab", async () => {
    renderWithApp(<SourcesPage />, { route: "/sources", path: "/sources" });
    expect((await screen.findAllByText("Not covered in corpus")).length).toBe(2);
  });

  it("explainers shows the active explainer and its reuse log", async () => {
    renderWithApp(<ExplainersPage />, { route: "/explainers", path: "/explainers" });
    expect(await screen.findByText("Canonical")).toBeInTheDocument();
    expect(screen.getByText("Reuse log")).toBeInTheDocument();
    expect(screen.getAllByText("Snapshot B").length).toBe(6);
  });
});
