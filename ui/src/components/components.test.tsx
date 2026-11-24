import { screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderWithApp } from "../test/render";
import { CitationLink } from "./CitationLink";
import { StaleBadge } from "./LastChecked";
import { RedactedText } from "./RedactedText";
import { checkStates, ReviewerChecklist } from "./ReviewerChecklist";
import { Counter, StatusPill } from "./ui";

describe("RedactedText", () => {
  it("renders tokens as chips and never parses HTML", () => {
    const { container } = renderWithApp(<RedactedText text="My PAN is [PAN] <b>bold</b>" />);
    expect(screen.getByText("[PAN]")).toHaveAttribute("title", "PAN removed before storage");
    expect(container.querySelector("b")).toBeNull();
    expect(container.textContent).toContain("<b>bold</b>");
  });
});

describe("CitationLink", () => {
  it("links allow-listed URLs once the policy loads", async () => {
    renderWithApp(<CitationLink url="https://groww.in/mutual-funds/hdfc-large-cap-fund-direct-growth" />);
    const link = await screen.findByRole("link");
    expect(link).toHaveAttribute("rel", "noopener noreferrer");
    expect(link).toHaveAttribute("target", "_blank");
  });
  it("renders other URLs as text, never as a link", async () => {
    renderWithApp(<CitationLink url="https://groww.in.example.com/hdfc-top-100-fund" />);
    await waitFor(() => expect(screen.getByTitle("Not on the public source allow-list")).toBeInTheDocument());
    expect(screen.queryByRole("link")).toBeNull();
  });
});

describe("ReviewerChecklist", () => {
  it("marks failures and warnings", () => {
    const states = checkStates({ failures: ["UNCITED_CLAIM", "PERFORMANCE"], warnings: ["MISSING_LAST_CHECKED"] });
    expect(states.filter((s) => s.state === "fail").map((s) => s.code)).toEqual(["UNCITED_CLAIM", "PERFORMANCE"]);
    expect(states.find((s) => s.code === "MISSING_LAST_CHECKED")?.state).toBe("warn");
  });
  it("summarises", () => {
    renderWithApp(<ReviewerChecklist result={{ failures: ["PII"] }} />);
    expect(screen.getByText("1 failed · 7 passed")).toBeInTheDocument();
    expect(screen.getByText("PII")).toBeInTheDocument();
  });
});

describe("Counter", () => {
  it("announces the cap and flags overflow", () => {
    renderWithApp(<Counter value={4} cap={3} unit="sentences" />);
    expect(screen.getByLabelText("4 of 3 sentences, over the cap")).toBeInTheDocument();
  });
});

describe("StatusPill", () => {
  it("shows text, not colour alone", () => {
    renderWithApp(<StatusPill status="blocked" label="Blocked" />);
    expect(screen.getByText("Blocked")).toBeInTheDocument();
  });
});

describe("StaleBadge", () => {
  it("only shows after 30 days", () => {
    const recent = new Date(Date.now() - 5 * 86_400_000).toISOString().slice(0, 10);
    const { container } = renderWithApp(<StaleBadge date={recent} />);
    expect(container.textContent).toBe("");
    renderWithApp(<StaleBadge date="2020-01-01" />);
    expect(screen.getByText(/days old/)).toBeInTheDocument();
  });
});
