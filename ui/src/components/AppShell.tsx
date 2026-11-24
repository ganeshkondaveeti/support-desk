import { BookOpenCheck, ChevronsLeft, ChevronsRight, ClipboardCheck, Database, Inbox, Info, LineChart, ShieldCheck, UserCircle2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { NavLink, Link } from "react-router-dom";

import { useHealth } from "../api/hooks";
import { DISCLAIMER, GATE_READ_ONLY, NAV } from "../copy";
import { cx } from "../lib/cx";
import { StaleBadge } from "./LastChecked";
import { TokenDialog } from "./TokenDialog";
import { Banner } from "./ui";

const ICONS = { "/queue": Inbox, "/pulse": LineChart, "/sources": Database, "/approvals": ClipboardCheck, "/explainers": BookOpenCheck } as const;

const COLLAPSE_KEY = "support-desk.sidebar-collapsed";

function readCollapsed(): boolean {
  try {
    return localStorage.getItem(COLLAPSE_KEY) === "1";
  } catch {
    return false;
  }
}

/** The disclaimer is on every screen and cannot be dismissed (brief §6, arch §13). */
export function DisclaimerBanner({ className }: { className?: string }) {
  return (
    <p
      data-testid="disclaimer"
      className={cx(
        "inline-flex items-center gap-1.5 rounded-full border border-warning-line bg-warning-bg px-2.5 py-0.5 text-body-sm font-medium text-warning-fg",
        className,
      )}
    >
      <Info aria-hidden className="h-3.5 w-3.5" />
      {DISCLAIMER}
    </p>
  );
}

export function AppShell({ children }: { children: ReactNode }) {
  const { data: health, isError } = useHealth();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const toggle = () => {
    setCollapsed((prev) => {
      try {
        localStorage.setItem(COLLAPSE_KEY, prev ? "0" : "1");
      } catch {
        // Preference only.
      }
      return !prev;
    });
  };
  const pending = health?.pending_approvals ?? 0;
  const checked = health?.corpus_last_checked;

  return (
    <div className="min-h-screen bg-canvas">
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-2 focus:top-2 focus:z-[60] focus:rounded focus:bg-surface focus:px-3 focus:py-2">
        Skip to content
      </a>
      <header className="fixed inset-x-0 top-0 z-40 flex h-header items-center justify-between gap-3 border-b border-line bg-surface/90 px-4 backdrop-blur">
        <div className="flex min-w-0 items-center gap-3">
          <Link to="/queue" className="flex shrink-0 items-center gap-2 text-headline-sm text-ink">
            <ShieldCheck aria-hidden className="h-5 w-5 text-primary" />
            Support Desk
          </Link>
          <DisclaimerBanner className="hidden sm:inline-flex" />
        </div>
        <div className="flex items-center gap-3">
          {checked ? (
            <span className="hidden items-center gap-1.5 font-mono text-label-sm text-ink-muted xl:inline-flex">
              Corpus last checked: A {checked.A} · B {checked.B}
              <StaleBadge date={checked.B} />
            </span>
          ) : null}
          <Link
            to="/approvals"
            className="inline-flex h-8 items-center gap-1.5 rounded bg-primary px-2.5 text-body-sm font-medium text-white hover:bg-primary-hover"
            aria-label={`Approvals, ${pending} pending`}
          >
            <ClipboardCheck aria-hidden className="h-4 w-4" />
            <span className="hidden sm:inline">Approvals</span>
            <span className="rounded-full bg-primary-hover px-1.5 font-mono text-label-sm tabular text-white">{pending}</span>
          </Link>
          <span className="hidden items-center gap-1.5 border-l border-line pl-3 text-body-sm text-ink md:inline-flex">
            <UserCircle2 aria-hidden className="h-6 w-6 text-primary" />
            Reviewer
          </span>
        </div>
      </header>

      {/* Below md the disclaimer moves under the header, still on every screen. */}
      <div className="fixed inset-x-0 top-header z-30 border-b border-line bg-surface px-4 py-1.5 sm:hidden">
        <DisclaimerBanner />
      </div>

      <aside
        className={cx(
          "fixed bottom-0 left-0 top-header z-30 hidden flex-col justify-between border-r border-line bg-surface py-3 md:flex",
          collapsed ? "w-sidebar-collapsed" : "w-sidebar",
        )}
        aria-label="Primary"
      >
        <nav className="flex flex-col gap-0.5 px-2">
          {NAV.map(({ to, label }) => {
            const Icon = ICONS[to];
            return (
              <NavLink
                key={to}
                to={to}
                title={collapsed ? label : undefined}
                className={({ isActive }) =>
                  cx(
                    "flex h-9 items-center gap-2.5 rounded px-2.5 text-body-lg",
                    isActive ? "bg-primary text-white" : "text-ink-muted hover:bg-subtle hover:text-ink",
                  )
                }
              >
                <Icon aria-hidden className="h-4 w-4 shrink-0" />
                <span className={cx("flex-1 truncate", collapsed && "sr-only")}>{label}</span>
                {to === "/approvals" && pending > 0 && !collapsed ? (
                  <span className="rounded-full bg-primary-wash px-1.5 font-mono text-label-sm text-primary-hover tabular">{pending}</span>
                ) : null}
              </NavLink>
            );
          })}
        </nav>
        <button
          type="button"
          onClick={toggle}
          className="mx-2 flex h-9 items-center gap-2.5 rounded px-2.5 text-body-md text-ink-muted hover:bg-subtle"
          aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
        >
          {collapsed ? <ChevronsRight aria-hidden className="h-4 w-4" /> : <ChevronsLeft aria-hidden className="h-4 w-4" />}
          {collapsed ? null : <span>Collapse sidebar</span>}
        </button>
      </aside>

      {/* Mobile nav: a scrollable tab row. */}
      <nav aria-label="Primary" className="fixed inset-x-0 top-[calc(var(--header-h)+2.25rem)] z-30 flex gap-1 overflow-x-auto border-b border-line bg-surface px-2 py-1.5 md:hidden">
        {NAV.map(({ to, label }) => (
          <NavLink
            key={to}
            to={to}
            className={({ isActive }) =>
              cx("whitespace-nowrap rounded px-2.5 py-1 text-body-sm", isActive ? "bg-primary text-white" : "text-ink-muted")
            }
          >
            {label}
          </NavLink>
        ))}
      </nav>

      <main
        id="main"
        tabIndex={-1}
        className={cx(
          "px-4 pb-10 pt-[calc(var(--header-h)+5.5rem)] focus:outline-none sm:pt-[calc(var(--header-h)+3.5rem)] md:px-6 md:pt-[calc(var(--header-h)+1.25rem)]",
          collapsed ? "md:pl-[calc(var(--sidebar-w-collapsed)+1.5rem)]" : "md:pl-[calc(var(--sidebar-w)+1.5rem)]",
        )}
      >
        <div className="mx-auto max-w-workbench space-y-3">
          {health?.gate_mode === "read_only" ? <Banner tone="warning">{GATE_READ_ONLY}</Banner> : null}
          {isError ? <Banner tone="critical" title="API unreachable">The console can't reach the server. Data shown may be out of date.</Banner> : null}
          {health && health.status === "degraded" ? (
            <Banner tone="warning" title="Service degraded">
              {health.corpus_loaded ? "" : "The facts corpus did not load. "}
              {health.db_reachable ? "" : "The database is unreachable."}
            </Banner>
          ) : null}
          {children}
        </div>
      </main>
      <TokenDialog />
    </div>
  );
}

export function PageHeader({ title, subtitle, eyebrow, actions, meta }: { title: string; subtitle: ReactNode; eyebrow?: ReactNode; actions?: ReactNode; meta?: ReactNode }) {
  return (
    <div className="flex flex-col gap-3 pb-2 lg:flex-row lg:items-end lg:justify-between">
      <div className="min-w-0">
        {eyebrow ? <div className="mb-1 font-mono text-label-sm uppercase text-primary">{eyebrow}</div> : null}
        <div className="flex flex-wrap items-center gap-2">
          <h1 className="text-headline-lg text-ink">{title}</h1>
          {meta}
        </div>
        <p className="mt-1 max-w-2xl text-body-lg font-normal text-ink-muted">{subtitle}</p>
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </div>
  );
}
