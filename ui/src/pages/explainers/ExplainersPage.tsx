import { BookOpenCheck, CheckCircle2, Clock } from "lucide-react";
import { Link } from "react-router-dom";

import { useExplainers } from "../../api/hooks";
import type { CanonicalExplainer, CitedBullet, ReuseEntry } from "../../api/types";
import { PageHeader } from "../../components/AppShell";
import { Badge, EmptyState, ErrorState, IdChip, LoadingBlock, SectionTitle } from "../../components/ui";
import { PAGE } from "../../copy";
import { formatDateTime } from "../../lib/format";
import { ExplainerPreview } from "../pulse/PulsePage";

function covers(explainer: CanonicalExplainer): { schemes: string[]; sections: string[] } {
  const pairs = (explainer.covers as unknown[]).filter((p): p is [string, string] => Array.isArray(p) && p.length === 2);
  return { schemes: [...new Set(pairs.map((p) => p[0]))], sections: [...new Set(pairs.map((p) => p[1]))] };
}

export default function ExplainersPage() {
  const explainers = useExplainers();
  return (
    <>
      <PageHeader title={PAGE.explainers.title} subtitle={PAGE.explainers.subtitle} />
      {explainers.isLoading ? (
        <LoadingBlock rows={8} label="Loading explainers" />
      ) : explainers.error ? (
        <ErrorState error={explainers.error} onRetry={() => void explainers.refetch()} />
      ) : explainers.data ? (
        <div className="space-y-6">
          {explainers.data.active ? (
            <ActiveExplainer explainer={explainers.data.active} />
          ) : (
            <EmptyState icon={BookOpenCheck} title="No canonical explainer yet" action={<Link to="/approvals" className="text-body-md font-medium text-primary hover:underline">Go to Approvals</Link>}>
              Approving a weekly run's explainer <em>doc append</em> makes it the canonical fee answer. Matching fee questions then reuse its bullets verbatim.
            </EmptyState>
          )}
          <History items={explainers.data.history} />
        </div>
      ) : null}
    </>
  );
}

function ActiveExplainer({ explainer }: { explainer: CanonicalExplainer }) {
  const { schemes, sections } = covers(explainer);
  const reuse = explainer.reuse_log as unknown as ReuseEntry[];
  return (
    <div className="grid grid-cols-1 gap-4 xl:grid-cols-[minmax(0,2fr)_minmax(320px,1fr)]">
      <section aria-labelledby="active-title" className="card overflow-hidden">
        <div className="space-y-3 border-b border-line bg-canvas p-4">
          <div className="flex flex-wrap items-center gap-2">
            <Badge tone="success">
              <CheckCircle2 aria-hidden className="h-3.5 w-3.5" /> Canonical
            </Badge>
            <h2 id="active-title" className="sr-only">
              Active explainer
            </h2>
            <IdChip value={explainer.id} display={`${explainer.id.slice(0, 18)}…`} />
            <span className="text-body-sm text-ink-muted">
              Active since <time dateTime={explainer.activated_at}>{formatDateTime(explainer.activated_at)}</time>
            </span>
          </div>
          {explainer.approved_action_id ? (
            <p className="text-body-sm text-ink-muted">
              Approval record:{" "}
              <Link to={`/approvals#${explainer.approved_action_id}`} className="font-mono text-label-md text-primary hover:underline" onClick={() => undefined}>
                {explainer.approved_action_id.slice(0, 22)}…
              </Link>{" "}
              <span className="text-ink-faint">(see the Written tab)</span>
            </p>
          ) : null}
          <dl className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <div>
              <dt className="eyebrow">Schemes</dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {schemes.map((s) => (
                  <Badge key={s} mono>
                    {s}
                  </Badge>
                ))}
              </dd>
            </div>
            <div>
              <dt className="eyebrow">Sections</dt>
              <dd className="mt-1 flex flex-wrap gap-1">
                {sections.map((s) => (
                  <Badge key={s}>{s}</Badge>
                ))}
              </dd>
            </div>
          </dl>
          <p className="text-body-sm text-ink-muted">Theme: <span className="font-mono">{explainer.theme_id}</span></p>
        </div>
        <div className="p-4">
          <ExplainerPreview
            bullets={explainer.bullets as unknown as CitedBullet[]}
            urls={explainer.source_urls as unknown as string[]}
            lastChecked={explainer.last_checked ?? ""}
          />
        </div>
      </section>

      <aside aria-labelledby="reuse-title" className="card self-start p-4">
        <div className="mb-3 flex items-center justify-between">
          <h2 id="reuse-title" className="text-headline-sm">
            Reuse log
          </h2>
          <Badge tone="primary" mono>
            {reuse.length} {reuse.length === 1 ? "reply" : "replies"}
          </Badge>
        </div>
        {reuse.length === 0 ? (
          <p className="text-body-md text-ink-muted">No ticket has reused this explainer yet.</p>
        ) : (
          <ol className="relative space-y-3 border-l border-line pl-4">
            {[...reuse].reverse().map((r, i) => (
              <li key={`${r.ticket_id}-${i}`} className="relative">
                <span aria-hidden className="absolute -left-[21px] top-1.5 h-2.5 w-2.5 rounded-full border-2 border-surface bg-primary" />
                <Link to={`/queue/${encodeURIComponent(r.ticket_id)}`} className="font-mono text-label-md text-primary hover:underline">
                  {r.ticket_id.length > 24 ? `${r.ticket_id.slice(0, 24)}…` : r.ticket_id}
                </Link>
                <p className="mt-0.5 flex items-center gap-1 text-body-sm text-ink-muted">
                  <Clock aria-hidden className="h-3.5 w-3.5" />
                  <time dateTime={r.ts}>{formatDateTime(r.ts)}</time>
                  {r.origin ? <span>· {r.origin}</span> : null}
                </p>
              </li>
            ))}
          </ol>
        )}
      </aside>
    </div>
  );
}

function History({ items }: { items: CanonicalExplainer[] }) {
  return (
    <section aria-labelledby="history-title">
      <SectionTitle id="history-title" aside="Kept for audit; never reused">
        Superseded explainers
      </SectionTitle>
      {items.length === 0 ? (
        <p className="card px-4 py-3 text-body-md text-ink-muted">No superseded explainers yet.</p>
      ) : (
        <div className="card overflow-x-auto">
          <table className="w-full min-w-[640px] border-collapse text-left">
            <thead className="bg-canvas">
              <tr className="border-b border-line">
                {["Explainer", "Active from", "Superseded", "Bullets", "Reuse count", "Approval"].map((h) => (
                  <th key={h} scope="col" className="px-3 py-2 text-label-sm font-semibold uppercase tracking-wide text-ink-faint">
                    {h}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {items.map((e) => (
                <tr key={e.id} className="h-11 border-b border-line/70 text-body-md last:border-0">
                  <td className="px-3">
                    <IdChip value={e.id} display={`${e.id.slice(0, 16)}…`} />
                  </td>
                  <td className="px-3 font-mono text-label-md text-ink-muted">{formatDateTime(e.activated_at)}</td>
                  <td className="px-3 font-mono text-label-md text-ink-muted">{formatDateTime(e.superseded_at)}</td>
                  <td className="px-3 tabular">{e.bullets.length}</td>
                  <td className="px-3 tabular">{e.reuse_log.length}</td>
                  <td className="px-3">
                    {e.approved_action_id ? (
                      <Link to={`/approvals#${e.approved_action_id}`} className="font-mono text-label-md text-primary hover:underline">
                        {e.approved_action_id.slice(0, 16)}…
                      </Link>
                    ) : (
                      "—"
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
