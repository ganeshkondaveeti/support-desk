import * as Tabs from "@radix-ui/react-tabs";
import { ArrowRight, ChevronDown, ChevronRight, CircleSlash } from "lucide-react";
import { Fragment, useMemo, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";

import { useSources } from "../../api/hooks";
import type { DriftRow, SourceBlock } from "../../api/types";
import { PageHeader } from "../../components/AppShell";
import { CitationLink } from "../../components/CitationLink";
import { StaleBadge } from "../../components/LastChecked";
import { Badge, Banner, ErrorState, LoadingBlock } from "../../components/ui";
import { PAGE, SECTIONS } from "../../copy";
import { cx } from "../../lib/cx";

/** Draft version ids look like `dv_{ticket_id}_v{n}`; link back to the ticket. */
function ticketIdFromDraft(draftId: string): string | null {
  const match = /^dv_(.+)_v\d+$/.exec(draftId);
  return match?.[1] ?? null;
}

export default function SourcesPage() {
  const location = useLocation();
  const navigate = useNavigate();
  const tab = location.pathname.endsWith("/drift") ? "drift" : "corpus";
  const sources = useSources();

  return (
    <>
      <PageHeader title={PAGE.sources.title} subtitle={PAGE.sources.subtitle} />
      {sources.isLoading ? (
        <LoadingBlock rows={10} label="Loading sources" />
      ) : sources.error ? (
        <ErrorState error={sources.error} onRetry={() => void sources.refetch()} />
      ) : sources.data ? (
        <Tabs.Root value={tab} onValueChange={(value) => navigate(value === "drift" ? "/sources/drift" : "/sources")}>
          <Tabs.List aria-label="Sources views" className="mb-3 flex gap-4 border-b border-line">
            <TabTrigger value="corpus">
              Corpus <Badge mono>{sources.data.blocks.length} blocks</Badge>
            </TabTrigger>
            <TabTrigger value="drift">
              Drift <Badge tone="warning" mono>{sources.data.drift.length}</Badge>
            </TabTrigger>
          </Tabs.List>
          <Tabs.Content value="corpus" className="focus:outline-none">
            <CorpusView blocks={sources.data.blocks} quarantined={sources.data.quarantined} />
          </Tabs.Content>
          <Tabs.Content value="drift" className="focus:outline-none">
            <DriftView rows={sources.data.drift} />
          </Tabs.Content>
        </Tabs.Root>
      ) : null}
    </>
  );
}

function TabTrigger({ value, children }: { value: string; children: React.ReactNode }) {
  return (
    <Tabs.Trigger
      value={value}
      className="-mb-px inline-flex items-center gap-2 border-b-2 border-transparent px-1 pb-2 text-body-lg text-ink-muted hover:text-ink data-[state=active]:border-primary data-[state=active]:text-ink"
    >
      {children}
    </Tabs.Trigger>
  );
}

interface SchemeGroup {
  schemeId: string;
  schemeName: string;
  sourceUrl: string;
  snapshots: Set<string>;
  blocks: Map<string, SourceBlock>;
}

function CorpusView({ blocks, quarantined }: { blocks: SourceBlock[]; quarantined: string[] }) {
  const [scheme, setScheme] = useState("");
  const [section, setSection] = useState("");
  const [snapshot, setSnapshot] = useState("");

  const groups = useMemo(() => {
    const map = new Map<string, SchemeGroup>();
    for (const b of blocks) {
      const g = map.get(b.scheme_id) ?? { schemeId: b.scheme_id, schemeName: b.scheme_name, sourceUrl: b.source_url, snapshots: new Set<string>(), blocks: new Map() };
      g.snapshots.add(b.snapshot);
      if (b.other_snapshot) g.snapshots.add(b.other_snapshot.snapshot);
      g.blocks.set(b.section, b);
      map.set(b.scheme_id, g);
    }
    return [...map.values()].sort((a, b) => a.schemeName.localeCompare(b.schemeName));
  }, [blocks]);

  const visible = groups.filter((g) => !scheme || g.schemeId === scheme);

  return (
    <div className="space-y-3">
      <div className="card flex flex-wrap items-center gap-3 px-3 py-2">
        <Select label="Scheme" value={scheme} onChange={setScheme} options={groups.map((g) => [g.schemeId, g.schemeName])} all={`All schemes (${groups.length})`} />
        <Select label="Section" value={section} onChange={setSection} options={SECTIONS.map((s) => [s, s])} all={`All sections (${SECTIONS.length})`} />
        <div role="group" aria-label="Snapshot" className="flex rounded border border-line bg-canvas p-0.5">
          {[
            ["", "All"],
            ["A", "A · 28-Jun"],
            ["B", "B · 01-Sep"],
          ].map(([value, label]) => (
            <button
              key={value}
              type="button"
              aria-pressed={snapshot === value}
              onClick={() => setSnapshot(value ?? "")}
              className={cx("rounded px-2.5 py-1 text-body-sm", snapshot === value ? "bg-surface text-ink shadow-1" : "text-ink-muted")}
            >
              {label}
            </button>
          ))}
        </div>
      </div>

      {quarantined.length > 0 ? (
        <Banner tone="critical" title={`${quarantined.length} quarantined sentences (never quoted)`}>
          <ul className="mt-1 list-disc space-y-0.5 pl-4">
            {quarantined.map((q) => (
              <li key={q}>{q}</li>
            ))}
          </ul>
        </Banner>
      ) : null}

      {visible.map((g) => (
        <SchemeCard key={g.schemeId} group={g} section={section} snapshot={snapshot} />
      ))}
    </div>
  );
}

function Select({ label, value, onChange, options, all }: { label: string; value: string; onChange: (v: string) => void; options: Array<[string, string]>; all: string }) {
  return (
    <label className="flex items-center gap-2">
      <span className="text-label-sm uppercase text-ink-faint">{label}</span>
      <select
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className="h-8 rounded border border-line-strong bg-surface px-2 text-body-md focus:border-primary focus:outline-none"
      >
        <option value="">{all}</option>
        {options.map(([v, l]) => (
          <option key={v} value={v}>
            {l}
          </option>
        ))}
      </select>
    </label>
  );
}

function SchemeCard({ group, section, snapshot }: { group: SchemeGroup; section: string; snapshot: string }) {
  const [open, setOpen] = useState(true);
  const [expanded, setExpanded] = useState<string | null>(null);
  const covered = group.blocks.size;
  const rows = SECTIONS.filter((s) => !section || s === section);

  return (
    <section className="card overflow-hidden" aria-labelledby={`scheme-${group.schemeId}`}>
      <header className="flex flex-wrap items-center gap-x-3 gap-y-1.5 border-b border-line bg-canvas px-3 py-2.5">
        <button type="button" onClick={() => setOpen(!open)} aria-expanded={open} aria-label={`${open ? "Collapse" : "Expand"} ${group.schemeName}`} className="rounded p-0.5 text-ink-muted hover:bg-subtle">
          <ChevronDown aria-hidden className={cx("h-4 w-4 transition-transform", !open && "-rotate-90")} />
        </button>
        <h2 id={`scheme-${group.schemeId}`} className="text-headline-sm text-ink">
          {group.schemeName}
        </h2>
        <Badge mono>{group.schemeId}</Badge>
        <CitationLink url={group.sourceUrl} />
        <span className="ml-auto flex items-center gap-1.5 text-body-sm text-ink-muted">
          Snapshots:
          {["A", "B"].map((s) => (
            <Badge key={s} tone={group.snapshots.has(s) ? (s === "B" ? "primary" : "neutral") : "neutral"} mono className={cx(!group.snapshots.has(s) && "line-through opacity-60")}>
              {s}
            </Badge>
          ))}
          <span className="ml-1">
            {covered} of {SECTIONS.length} sections covered
          </span>
        </span>
      </header>
      {open ? (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] border-collapse text-left">
            <thead>
              <tr className="border-b border-line">
                {["Section", "Verbatim text (resolved)", "Snapshot", "NAV date", "Last checked", "Cited by"].map((h) => (
                  <th key={h} scope="col" className="px-3 py-2 text-label-sm font-semibold uppercase tracking-wide text-ink-faint">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {rows.map((name) => {
                const b = group.blocks.get(name);
                if (!b) {
                  return (
                    <tr key={name} className="border-b border-line/70 text-ink-faint last:border-0">
                      <td className="px-3 py-2.5 text-body-md line-through">{name}</td>
                      <td colSpan={5} className="px-3 py-2.5 text-body-sm italic">
                        <span className="inline-flex items-center gap-1.5">
                          <CircleSlash aria-hidden className="h-3.5 w-3.5" /> Not covered in corpus
                        </span>
                      </td>
                    </tr>
                  );
                }
                const hiddenBySnapshot = snapshot && b.snapshot !== snapshot && b.other_snapshot?.snapshot !== snapshot;
                if (hiddenBySnapshot) return null;
                const cited = b.citing_draft_ids.length + b.citing_explainer_ids.length;
                const isOpen = expanded === name;
                const shown = snapshot && b.snapshot !== snapshot && b.other_snapshot ? b.other_snapshot : b;
                return (
                  <Fragment key={name}>
                    <tr className={cx("border-b border-line/70 align-top last:border-0", isOpen && "bg-primary-wash/50")}>
                      <td className="px-3 py-2.5">
                        <button
                          type="button"
                          onClick={() => setExpanded(isOpen ? null : name)}
                          aria-expanded={isOpen}
                          className="inline-flex items-center gap-1 text-left text-body-md font-medium text-ink hover:text-primary"
                        >
                          <ChevronRight aria-hidden className={cx("h-3.5 w-3.5 shrink-0 transition-transform", isOpen && "rotate-90")} />
                          {name}
                        </button>
                      </td>
                      <td className="max-w-[36rem] px-3 py-2.5 text-body-md text-ink">“{shown.text}”</td>
                      <td className="px-3 py-2.5">
                        <Badge tone={shown.snapshot === "B" ? "primary" : "neutral"} mono>
                          {shown.snapshot}
                        </Badge>
                      </td>
                      <td className="whitespace-nowrap px-3 py-2.5 font-mono text-label-md text-ink-muted">{shown.nav_date || "—"}</td>
                      <td className="whitespace-nowrap px-3 py-2.5">
                        <span className="flex flex-col items-start gap-1">
                          <span className="font-mono text-label-md text-ink-muted">{shown.last_checked}</span>
                          <StaleBadge date={shown.last_checked} />
                        </span>
                      </td>
                      <td className="px-3 py-2.5">
                        <Badge tone={cited ? "primary" : "neutral"} mono>
                          {cited}
                        </Badge>
                      </td>
                    </tr>
                    {isOpen ? (
                      <tr className="border-b border-line/70 bg-primary-wash/30">
                        <td colSpan={6} className="px-3 py-3">
                          <BlockDrawer block={b} />
                        </td>
                      </tr>
                    ) : null}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </div>
      ) : null}
    </section>
  );
}

function BlockDrawer({ block }: { block: SourceBlock }) {
  const other = block.other_snapshot;
  const identical = other ? other.text.trim() === block.text.trim() : false;
  const tickets = block.citing_draft_ids.map(ticketIdFromDraft).filter((t): t is string => Boolean(t));
  const uniqueTickets = [...new Set(tickets)];
  return (
    <div className="space-y-3">
      <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
        <div className="rounded border border-primary-line bg-surface p-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <p className="eyebrow">Resolved (used in replies)</p>
            <Badge tone="primary" mono>
              {block.snapshot} · {block.last_checked}
            </Badge>
          </div>
          <p className="text-body-md text-ink">“{block.text}”</p>
        </div>
        <div className="rounded border border-line bg-surface p-3">
          <div className="mb-1.5 flex items-center justify-between gap-2">
            <p className="eyebrow">Other snapshot</p>
            {other ? (
              <span className="flex items-center gap-1.5">
                <Badge tone={identical ? "success" : "warning"}>{identical ? "identical" : "changed"}</Badge>
                <Badge mono>
                  {other.snapshot} · {other.last_checked}
                </Badge>
              </span>
            ) : null}
          </div>
          <p className="text-body-md text-ink-muted">{other ? `“${other.text}”` : "Only one snapshot has this section."}</p>
        </div>
      </div>
      <div className="flex flex-wrap items-center gap-2 text-body-sm text-ink-muted">
        <span>Cited by:</span>
        {uniqueTickets.length === 0 && block.citing_explainer_ids.length === 0 ? <span className="text-ink-faint">nothing yet</span> : null}
        {uniqueTickets.map((t) => (
          <Link key={t} to={`/queue/${encodeURIComponent(t)}`} className="inline-flex items-center gap-1 rounded-sm border border-line bg-surface px-1.5 font-mono text-label-sm text-primary hover:underline">
            {t.length > 20 ? `${t.slice(0, 20)}…` : t}
          </Link>
        ))}
        {block.citing_explainer_ids.length > 0 ? (
          <Link to="/explainers" className="inline-flex items-center gap-1 text-primary hover:underline">
            {block.citing_explainer_ids.length} explainer {block.citing_explainer_ids.length === 1 ? "bullet" : "bullets"} <ArrowRight aria-hidden className="h-3.5 w-3.5" />
          </Link>
        ) : null}
      </div>
    </div>
  );
}

function Change({ a, b }: { a: string | null | undefined; b: string | null | undefined }) {
  if (!a && !b) return <span className="text-ink-faint">—</span>;
  if (!b) return <span className="text-ink-faint">{a} → not in B</span>;
  if (!a) return <span className="text-ink-faint">not in A → {b}</span>;
  const changed = a !== b;
  return (
    <span className={cx("whitespace-nowrap", changed ? "text-ink" : "text-ink-muted")}>
      {a} <span className={changed ? "font-semibold text-warning-solid" : "text-ink-faint"}>→</span> {b}
      {changed ? <span className="sr-only"> (changed)</span> : null}
    </span>
  );
}

function DriftView({ rows }: { rows: DriftRow[] }) {
  const allExitUnchanged = rows.every((r) => r.exit_load === "unchanged");
  return (
    <div className="space-y-3">
      <Banner tone="info">
        Drift is informational. Replies always quote the resolved (newest) snapshot with its own date.
        {allExitUnchanged ? " Exit loads are identical across snapshots." : ""}
      </Banner>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[760px] border-collapse text-left">
          <thead className="bg-canvas">
            <tr className="border-b border-line">
              {["Scheme", "Expense ratio A → B", "Fund size A → B", "Riskometer A → B", "Exit load"].map((h) => (
                <th key={h} scope="col" className="px-3 py-2 text-label-sm font-semibold uppercase tracking-wide text-ink-faint">
                  {h}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.scheme_id} className="h-11 border-b border-line/70 text-body-md last:border-0">
                <td className="px-3 font-mono text-label-md text-ink">{r.scheme_id}</td>
                <td className="px-3 tabular">
                  <Change a={r.expense_ratio_a} b={r.expense_ratio_b} />
                </td>
                <td className="px-3 tabular">
                  <Change a={r.fund_size_a} b={r.fund_size_b} />
                </td>
                <td className="px-3">
                  <Change a={r.riskometer_a} b={r.riskometer_b} />
                </td>
                <td className="px-3">
                  <Badge tone={r.exit_load === "unchanged" ? "success" : "warning"}>{r.exit_load}</Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
