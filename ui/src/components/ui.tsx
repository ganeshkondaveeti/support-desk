import { AlertTriangle, Check, Copy, Info, OctagonAlert, RotateCw, type LucideIcon } from "lucide-react";
import { forwardRef, useState, type ButtonHTMLAttributes, type ReactNode } from "react";

import { cx } from "../lib/cx";

/* ------------------------------------------------------------------ Button */

type ButtonVariant = "primary" | "secondary" | "destructive" | "ghost";

const BUTTON: Record<ButtonVariant, string> = {
  primary:
    "bg-primary text-white border-primary hover:bg-primary-hover shadow-[inset_0_1px_0_0_rgb(255_255_255/0.15)] disabled:bg-primary/50 disabled:border-transparent",
  secondary: "bg-surface text-ink-muted border-line hover:bg-canvas hover:text-ink disabled:text-ink-faint",
  destructive: "bg-critical-bg text-critical-fg border-critical-line hover:bg-critical-line/40 disabled:opacity-60",
  ghost: "bg-transparent text-ink-muted border-transparent hover:bg-subtle hover:text-ink disabled:text-ink-faint",
};

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: "sm" | "md";
  icon?: LucideIcon;
  busy?: boolean;
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon: Icon, busy = false, className, children, disabled, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      disabled={disabled || busy}
      aria-busy={busy || undefined}
      className={cx(
        "inline-flex shrink-0 items-center justify-center gap-1.5 rounded border font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" ? "h-8 px-2.5 text-body-sm" : "h-9 px-3 text-body-md",
        BUTTON[variant],
        className,
      )}
      {...rest}
    >
      {busy ? <RotateCw aria-hidden className="h-4 w-4 animate-spin" /> : Icon ? <Icon aria-hidden className="h-4 w-4" /> : null}
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------ Badges */

export type Tone = "neutral" | "primary" | "success" | "warning" | "critical" | "info";

const TONE: Record<Tone, string> = {
  neutral: "bg-subtle text-ink-muted border-line",
  primary: "bg-primary-wash text-primary-hover border-primary-line",
  success: "bg-success-bg text-success-fg border-success-line",
  warning: "bg-warning-bg text-warning-fg border-warning-line",
  critical: "bg-critical-bg text-critical-fg border-critical-line",
  info: "bg-info-bg text-info-fg border-info-line",
};

const DOT: Record<Tone, string> = {
  neutral: "bg-ink-faint",
  primary: "bg-primary",
  success: "bg-success-solid",
  warning: "bg-warning-solid",
  critical: "bg-critical-solid",
  info: "bg-info-solid",
};

export function Badge({
  tone = "neutral",
  dot = false,
  mono = false,
  className,
  children,
  title,
}: {
  tone?: Tone;
  dot?: boolean;
  mono?: boolean;
  className?: string;
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      title={title}
      className={cx(
        "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-px font-medium",
        mono ? "font-mono text-label-sm" : "text-body-sm",
        TONE[tone],
        className,
      )}
    >
      {dot ? <span aria-hidden className={cx("h-1.5 w-1.5 rounded-full", DOT[tone])} /> : null}
      {children}
    </span>
  );
}

const STATUS_TONE: Record<string, Tone> = {
  awaiting_human: "warning",
  processing: "info",
  blocked: "critical",
  sent: "success",
  rejected: "neutral",
  failed: "critical",
  pending: "warning",
  approved: "info",
  executing: "info",
  executed: "success",
  superseded: "neutral",
  awaiting_approval: "warning",
  done: "success",
  running: "info",
  pass: "success",
};

/** Status shown as dot + text, never colour alone (plan P9.3). */
export function StatusPill({ status, label }: { status: string; label?: string }) {
  return (
    <Badge tone={STATUS_TONE[status] ?? "neutral"} dot>
      {label ?? status.replace(/_/g, " ")}
    </Badge>
  );
}

export function PriorityPill({ priority }: { priority: string | null | undefined }) {
  if (!priority) return <span className="text-ink-faint">—</span>;
  const tone: Tone = priority === "P1" ? "critical" : priority === "P2" ? "warning" : "neutral";
  return (
    <Badge tone={tone} mono title={`Priority ${priority}`}>
      {priority}
    </Badge>
  );
}

const TREND_TONE: Record<string, Tone> = { rising: "warning", recurring: "info", one_off: "neutral", insufficient_data: "neutral" };

export function TrendPill({ trend }: { trend: string }) {
  return <Badge tone={TREND_TONE[trend] ?? "neutral"}>{trend.replace(/_/g, "-")}</Badge>;
}

/** Monospace identifier with click-to-copy. */
export function IdChip({ value, display, className }: { value: string; display?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1200);
    } catch {
      // Clipboard blocked: the full id is still in the title.
    }
  };
  return (
    <button
      type="button"
      onClick={copy}
      title={`${value} (click to copy)`}
      // The accessible name starts with the visible text (WCAG 2.5.3).
      aria-label={`${display ?? value}, copy full id`}
      className={cx(
        "group inline-flex max-w-full items-center gap-1 rounded-sm border border-line bg-subtle px-1.5 py-px font-mono text-label-sm text-ink-muted hover:border-line-strong",
        className,
      )}
    >
      <span className="truncate">{display ?? value}</span>
      {copied ? (
        <Check aria-hidden className="h-3 w-3 text-success-fg" />
      ) : (
        <Copy aria-hidden className="h-3 w-3 opacity-0 group-hover:opacity-100" />
      )}
    </button>
  );
}

/* ----------------------------------------------------------------- Counter */

/** `n / cap unit`. Turns critical only above the cap (plan P9.3). */
export function Counter({ value, cap, unit }: { value: number; cap: number; unit?: string }) {
  const over = value > cap;
  return (
    <span
      className={cx(
        "inline-flex items-center rounded-full border px-2 py-px font-mono text-label-sm tabular",
        over ? TONE.critical : value === cap ? TONE.primary : TONE.neutral,
      )}
      aria-label={`${value} of ${cap}${unit ? ` ${unit}` : ""}${over ? ", over the cap" : ""}`}
    >
      {value} / {cap}
      {unit ? ` ${unit}` : ""}
    </span>
  );
}

export function ProgressBar({ value, max, label }: { value: number; max: number; label: string }) {
  const pct = Math.min(100, Math.round((value / max) * 100));
  return (
    <div role="progressbar" aria-label={label} aria-valuenow={value} aria-valuemin={0} aria-valuemax={max} className="h-1.5 w-full rounded-full bg-subtle">
      <div className={cx("h-1.5 rounded-full", value >= max ? "bg-critical-solid" : "bg-primary")} style={{ width: `${pct}%` }} />
    </div>
  );
}

/* ------------------------------------------------------------------ Banner */

const BANNER_ICON: Record<"info" | "warning" | "critical" | "success", LucideIcon> = {
  info: Info,
  warning: AlertTriangle,
  critical: OctagonAlert,
  success: Check,
};

export function Banner({
  tone,
  title,
  children,
  action,
  className,
}: {
  tone: "info" | "warning" | "critical" | "success";
  title?: ReactNode;
  children?: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  const Icon = BANNER_ICON[tone];
  return (
    <div role={tone === "critical" ? "alert" : "status"} className={cx("flex items-start gap-2.5 rounded-md border px-3 py-2.5", TONE[tone], className)}>
      <Icon aria-hidden className="mt-0.5 h-4 w-4 shrink-0" />
      <div className="min-w-0 flex-1 text-body-md">
        {title ? <p className="font-semibold">{title}</p> : null}
        {children ? <div className={cx(title ? "mt-0.5" : undefined)}>{children}</div> : null}
      </div>
      {action}
    </div>
  );
}

/* --------------------------------------------------------- Cards, sections */

export function AuditCard({
  title,
  aside,
  children,
  className,
  bodyClassName,
}: {
  title: ReactNode;
  aside?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}) {
  return (
    <section className={cx("card overflow-hidden", className)}>
      <header className="flex min-h-8 items-center justify-between gap-2 border-b border-line bg-canvas px-3 py-1.5">
        <h3 className="eyebrow">{title}</h3>
        {aside}
      </header>
      <div className={cx("p-3", bodyClassName)}>{children}</div>
    </section>
  );
}

export function SectionTitle({ children, aside, id }: { children: ReactNode; aside?: ReactNode; id?: string }) {
  return (
    <div className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
      <h2 id={id} className="text-headline-sm text-ink">
        {children}
      </h2>
      {aside ? <div className="text-body-sm text-ink-faint">{aside}</div> : null}
    </div>
  );
}

export function KeyValue({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-label-sm uppercase tracking-wide text-ink-muted">{label}</dt>
      <dd className="mt-0.5 text-body-md text-ink">{children}</dd>
    </div>
  );
}

/** Keeps review and ticket windows visibly separate (arch §7.1, two clocks). */
export function WindowLabel({ kind, value }: { kind: "review" | "ticket"; value: string }) {
  return (
    <div className="rounded border border-line bg-surface px-2.5 py-1.5">
      <p className="text-label-sm uppercase tracking-wide text-ink-faint">{kind === "review" ? "Review window" : "Ticket window"}</p>
      <p className="font-mono text-label-md text-ink">{value}</p>
    </div>
  );
}

/* ------------------------------------------------------ Empty/load/error */

export function EmptyState({ icon: Icon, title, children, action }: { icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="card flex flex-col items-center justify-center gap-2 px-6 py-12 text-center">
      <span className="flex h-10 w-10 items-center justify-center rounded-full bg-primary-wash text-primary">
        <Icon aria-hidden className="h-5 w-5" />
      </span>
      <h2 className="text-headline-sm">{title}</h2>
      {children ? <div className="max-w-md text-body-md text-ink-muted">{children}</div> : null}
      {action ? <div className="mt-2 flex flex-wrap justify-center gap-2">{action}</div> : null}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div aria-hidden className={cx("animate-pulse rounded bg-subtle", className)} />;
}

export function LoadingBlock({ rows = 4, label = "Loading" }: { rows?: number; label?: string }) {
  return (
    <div role="status" aria-label={label} className="card space-y-2 p-4">
      {Array.from({ length: rows }, (_, i) => (
        <Skeleton key={i} className={cx("h-4", i % 3 === 2 ? "w-2/3" : "w-full")} />
      ))}
      <span className="sr-only">{label}…</span>
    </div>
  );
}

export function ErrorState({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  return (
    <Banner
      tone="critical"
      title="Couldn't load this view"
      action={
        onRetry ? (
          <Button size="sm" icon={RotateCw} onClick={onRetry}>
            Retry
          </Button>
        ) : undefined
      }
    >
      {message}
    </Banner>
  );
}
