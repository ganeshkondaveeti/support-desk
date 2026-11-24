import { ArrowRight, BarChart3, ChevronDown, FileText, LineChart, ListChecks, MessageSquareQuote, Play } from "lucide-react";
import { Fragment, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";

import { ApiError } from "../../api/client";
import { useActions, useRun, useRunWeekly, useRuns } from "../../api/hooks";
import type { Action, CitedBullet, Quote, RepeatQuestion, Theme, WeeklyRunDetail } from "../../api/types";
import { PageHeader } from "../../components/AppShell";
import { CitationLink } from "../../components/CitationLink";
import { LastChecked } from "../../components/LastChecked";
import { PlainMarkdown } from "../../components/PlainMarkdown";
import { RedactedText } from "../../components/RedactedText";
import { Badge, Banner, Button, Counter, EmptyState, ErrorState, IdChip, LoadingBlock, SectionTitle, StatusPill, TrendPill } from "../../components/ui";
import { ACTION_KIND, ACTION_STATUS, CAPS, EXPLAINER_SCOPE, PAGE, SUBJECT } from "../../copy";
import { cx } from "../../lib/cx";
import { formatDate, formatDateTime, formatWindow, formatWindowString } from "../../lib/format";

export default function PulsePage() {
  const { runId } = useParams();
  const navigate = useNavigate();
  const runs = useRuns();
  const selectedId = runId ?? runs.data?.items[0]?.id;
  const run = useRun(selectedId);
  const runNow = useRunWeekly();

  const trigger = () =>
    runNow.mutate(undefined, {
      onSuccess: (result) => navigate(`/pulse/${encodeURIComponent(result.run_id)}`),
    });

  return (
    <>
      <PageHeader
        eyebrow="Weekly loop"
        title={PAGE.pulse.title}
        subtitle={PAGE.pulse.subtitle}
        actions={
          <>
            {runs.data && runs.data.items.length > 0 ? (
              <label className="flex items-center gap-2">
                <span className="sr-only">Weekly run</span>
                <select
                  value={selectedId ?? ""}
                  onChange={(event) => navigate(`/pulse/${encodeURIComponent(event.target.value)}`)}
                  className="h-9 max-w-[22rem] rounded border border-line-strong bg-surface px-2 text-body-md focus:border-primary focus:outline-none"
                >
                  {runs.data.items.map((r) => (
                    <option key={r.id} value={r.id}>
                      Week ending {formatDate(r.as_of)} · {r.trigger} · {formatDateTime(r.started_at)}
                    </option>
                  ))}
                </select>
              </label>
            ) : null}
            <Button variant="primary" icon={Play} busy={runNow.isPending} onClick={trigger}>
              Run weekly loop now
            </Button>
          </>
        }
      />
      {runNow.data?.noop ? (
        <Banner tone="info">This week's input was already processed, so the existing run was kept (no duplicate actions).</Banner>
      ) : null}
      {runNow.error ? (
        <Banner tone={runNow.error instanceof ApiError && runNow.error.code === "weekly_run_locked" ? "warning" : "critical"}>
          {runNow.error instanceof Error ? runNow.error.message : "The run failed."}
        </Banner>
      ) : null}

      {runs.isLoading || (selectedId && run.isLoading) ? (
        <LoadingBlock rows={10} label="Loading weekly run" />
      ) : runs.error ? (
        <ErrorState error={runs.error} onRetry={() => void runs.refetch()} />
      ) : !selectedId ? (
        <EmptyState
          icon={LineChart}
          title="No weekly run yet"
          action={
            <Button variant="primary" icon={Play} busy={runNow.isPending} onClick={trigger}>
              Run weekly loop now
            </Button>
          }
        >
          Runs automatically every Monday 09:00 IST, or start one now.
        </EmptyState>
      ) : run.error ? (
        <ErrorState error={run.error} onRetry={() => void run.refetch()} />
      ) : run.data ? (
        <RunView run={run.data} />
      ) : null}
    </>
  );
}

function RunView({ run }: { run: WeeklyRunDetail }) {
  const actions = useActions("all");
  const themes = [...run.themes].sort((a, b) => b.count - a.count);
  const reviews = themes.reduce((sum, t) => sum + t.count, 0);
  const first = themes[0];
  const window = first ? formatWindow(first.window_start, first.window_end) : "—";
  const subjects = new Set([run.pulse?.id, run.explainer?.id].filter(Boolean));
  const runActions = (actions.data?.items ?? []).filter((a) => subjects.has(a.subject_id));
  const pendingHere = runActions.filter((a) => a.status === "pending").length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2 text-body-sm text-ink-muted">
        <IdChip value={run.id} display={`${run.id.slice(0, 16)}…`} />
        <StatusPill status={run.status} />
        <span>
          {run.trigger} · started {formatDateTime(run.started_at)}
        </span>
        {run.error ? <Badge tone="warning">{run.error}</Badge> : null}
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Kpi icon={MessageSquareQuote} label="Reviews analysed" value={reviews.toLocaleString("en-IN")} sub={window} />
        <Kpi icon={BarChart3} label="Themes" value={String(themes.length)} sub={window} />
        <Kpi
          icon={FileText}
          label="Pulse"
          value={run.pulse ? `${run.pulse.word_count} / ${CAPS.pulseWords} words` : "—"}
          sub={`week ending ${formatDate(run.as_of)}`}
          alert={Boolean(run.pulse && run.pulse.word_count > CAPS.pulseWords)}
        />
        <Kpi icon={ListChecks} label="Pending actions" value={String(pendingHere)} sub="from this run" />
      </div>

      <section aria-labelledby="themes-title">
        <SectionTitle id="themes-title" aside="Sorted by review count">
          Themes <Badge mono className="ml-1 align-middle">{themes.length} clusters</Badge>
        </SectionTitle>
        <ThemesTable themes={themes} />
      </section>

      <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,3fr)_minmax(0,2fr)]">
        <section aria-labelledby="pulse-title" className="card p-5">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 id="pulse-title" className="eyebrow">
              Pulse preview
            </h2>
            {run.pulse ? <Counter value={run.pulse.word_count} cap={CAPS.pulseWords} unit="words" /> : null}
          </div>
          {run.pulse ? (
            <>
              <div className="max-w-[72ch]">
                <PlainMarkdown source={run.pulse.body_md} />
              </div>
              <QuoteChips quotes={run.pulse.quotes as Quote[]} />
              <RepeatQuestions items={run.pulse.repeat_questions as RepeatQuestion[]} />
            </>
          ) : (
            <p className="text-body-md text-ink-muted">This run has no pulse.</p>
          )}
        </section>

        <section aria-labelledby="explainer-title" className="card self-start p-5">
          <div className="mb-3 flex items-center justify-between gap-2">
            <h2 id="explainer-title" className="text-headline-sm">
              Fee explainer preview
            </h2>
            {run.explainer ? <Counter value={run.explainer.bullets.length} cap={CAPS.explainerBullets} unit="bullets" /> : null}
          </div>
          {run.explainer ? <ExplainerPreview bullets={run.explainer.bullets as CitedBullet[]} lastChecked={run.explainer.last_checked} urls={run.explainer.source_urls as string[]} /> : <p className="text-body-md text-ink-muted">This run has no explainer.</p>}
        </section>
      </div>

      <section aria-labelledby="run-actions-title">
        <SectionTitle id="run-actions-title" aside="Each one needs its own approval">
          This run proposed {runActions.length} {runActions.length === 1 ? "action" : "actions"}
        </SectionTitle>
        {actions.isLoading ? (
          <LoadingBlock rows={2} />
        ) : runActions.length === 0 ? (
          <p className="text-body-md text-ink-muted">No actions recorded for this run.</p>
        ) : (
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
            {runActions.map((a) => (
              <RunActionCard key={a.id} action={a} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function Kpi({ icon: Icon, label, value, sub, alert = false }: { icon: typeof FileText; label: string; value: string; sub: string; alert?: boolean }) {
  return (
    <div className={cx("card p-4", alert && "border-critical-line")}>
      <div className="flex items-center justify-between">
        <p className="eyebrow">{label}</p>
        <Icon aria-hidden className="h-4 w-4 text-primary" />
      </div>
      <p className="mt-2 text-headline-lg tabular text-ink">{value}</p>
      <p className="mt-0.5 font-mono text-label-sm text-ink-faint">{sub}</p>
    </div>
  );
}

const MAX_CHIPS = 24;

function ThemesTable({ themes }: { themes: Theme[] }) {
  const [open, setOpen] = useState<string | null>(null);
  return (
    <div className="card overflow-x-auto">
      <table className="w-full min-w-[760px] border-collapse text-left">
        <thead className="bg-canvas">
          <tr className="border-b border-line">
            {["Theme id", "Name", "Reviews", "Avg rating", "Trend", "Window", "Review ids"].map((h) => (
              <th key={h} scope="col" className={cx("px-3 py-2 text-label-sm font-semibold uppercase tracking-wide text-ink-faint", (h === "Reviews" || h === "Avg rating") && "text-right")}>
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {themes.map((t) => {
            const expanded = open === t.theme_id;
            const ids = t.review_ids.map(String);
            return (
              <Fragment key={t.theme_id}>
                <tr className={cx("h-11 border-b border-line/70", expanded && "bg-primary-wash/60")}>
                  <td className="px-3 font-mono text-label-md text-ink-muted">{t.theme_id}</td>
                  <td className="px-3 text-body-md text-ink">
                    {t.name}
                    {t.is_fee_theme ? <Badge tone="info" className="ml-2">fee theme</Badge> : null}
                    {t.is_top3 ? <Badge tone="primary" className="ml-2">top 3</Badge> : null}
                  </td>
                  <td className="px-3 text-right tabular text-body-md">{t.count.toLocaleString("en-IN")}</td>
                  <td className="px-3 text-right tabular text-body-md">{t.avg_rating.toFixed(1)}★</td>
                  <td className="px-3">
                    <TrendPill trend={t.trend} />
                  </td>
                  <td className="whitespace-nowrap px-3 font-mono text-label-sm text-ink-muted">{formatWindow(t.window_start, t.window_end)}</td>
                  <td className="px-3">
                    <Button size="sm" variant={expanded ? "primary" : "secondary"} aria-expanded={expanded} onClick={() => setOpen(expanded ? null : t.theme_id)}>
                      {expanded ? "Collapse" : `Show ${ids.length}`}
                      <ChevronDown aria-hidden className={cx("h-3.5 w-3.5 transition-transform", expanded && "rotate-180")} />
                    </Button>
                  </td>
                </tr>
                {expanded ? (
                  <tr className="border-b border-line/70 bg-primary-wash/40">
                    <td colSpan={7} className="px-3 py-2.5">
                      <div className="flex flex-wrap items-center gap-1">
                        <span className="mr-1 text-body-sm text-ink-faint">Review ids ({formatWindow(t.window_start, t.window_end)}):</span>
                        {ids.slice(0, MAX_CHIPS).map((rid) => (
                          <IdChip key={rid} value={rid} />
                        ))}
                        {ids.length > MAX_CHIPS ? <Badge mono>+{ids.length - MAX_CHIPS} more</Badge> : null}
                      </div>
                    </td>
                  </tr>
                ) : null}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function QuoteChips({ quotes }: { quotes: Quote[] }) {
  if (!quotes?.length) return null;
  return (
    <div className="mt-4 flex flex-wrap items-center gap-1.5 border-t border-line pt-3">
      <span className="text-body-sm text-ink-faint">Quoted reviews:</span>
      {quotes.map((q) => (
        <span key={q.review_id} title={q.text}>
          <IdChip value={q.review_id} />
        </span>
      ))}
    </div>
  );
}

/** Structured view of repeat questions; shown only when there are any (the pulse body says "none"). */
function RepeatQuestions({ items }: { items: RepeatQuestion[] }) {
  if (!items?.length) return null;
  return (
    <div className="mt-4 border-t border-line pt-3">
      <p className="eyebrow mb-2">Repeat questions (ticket clock)</p>
      <ul className="space-y-1.5">
        {items.map((q, i) => (
          <li key={`${q.representative_text}-${i}`} className="flex items-center justify-between gap-3 rounded border border-info-line bg-info-bg px-3 py-2">
            <span className="text-body-md text-ink">
              <RedactedText text={q.representative_text} />
              {q.possible_duplicate_burst ? <Badge tone="warning" className="ml-2">possible burst</Badge> : null}
            </span>
            <span className="flex shrink-0 items-center gap-2">
              <span className="font-mono text-label-sm text-ink-faint">{formatWindowString(q.window ?? (q.window_start && q.window_end ? `${q.window_start} → ${q.window_end}` : undefined))}</span>
              <Badge tone="warning" mono>
                {q.count}×
              </Badge>
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ExplainerPreview({ bullets, lastChecked, urls }: { bullets: CitedBullet[]; lastChecked: string; urls: string[] }) {
  return (
    <>
      <p className="rounded border border-info-line bg-info-bg px-3 py-2 text-body-md text-ink">{EXPLAINER_SCOPE}</p>
      <ol className="mt-3 space-y-2">
        {bullets.map((b, i) => (
          <li key={`${b.text}-${i}`} className="flex flex-col gap-1 rounded border border-line px-3 py-2">
            <span className="flex gap-2 text-body-md text-ink">
              <span className="font-mono text-label-md text-primary">{String(i + 1).padStart(2, "0")}</span>
              {b.text}
            </span>
            <span className="flex flex-wrap items-center gap-1.5 pl-7">
              {b.scheme_id ? <Badge mono>{b.scheme_id}</Badge> : null}
              <CitationLink url={b.source_url} label={b.section ? `groww.in · ${b.section}` : undefined} />
              {b.snapshot ? <Badge mono>Snapshot {b.snapshot}</Badge> : null}
            </span>
          </li>
        ))}
      </ol>
      <div className="mt-3 flex flex-col gap-1.5 border-t border-line pt-3">
        <p className="text-body-sm text-ink-faint">Sources ({urls.length}):</p>
        {urls.map((u) => (
          <CitationLink key={u} url={u} />
        ))}
        <LastChecked date={lastChecked} />
      </div>
    </>
  );
}

function RunActionCard({ action }: { action: Action }) {
  return (
    <div className="card flex flex-col gap-2 p-4">
      <p className="text-headline-sm text-ink">
        {SUBJECT[action.subject_type] ?? action.subject_type} → {ACTION_KIND[action.kind] ?? action.kind}
      </p>
      <StatusPill status={action.status} label={ACTION_STATUS[action.status]} />
      <Link to={`/approvals#${action.id}`} className="mt-auto inline-flex items-center gap-1 text-body-md font-medium text-primary hover:underline">
        Review in Approvals <ArrowRight aria-hidden className="h-4 w-4" />
      </Link>
    </div>
  );
}

