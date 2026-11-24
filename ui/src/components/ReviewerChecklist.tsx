import { AlertTriangle, CheckCircle2, XCircle } from "lucide-react";

import type { ReviewerResult } from "../api/types";
import { REVIEWER_CHECKS } from "../copy";
import { cx } from "../lib/cx";

type State = "pass" | "fail" | "warn";

export function checkStates(result: ReviewerResult | undefined): Array<{ code: string; label: string; state: State }> {
  const failures = new Set(result?.failures ?? result?.flags ?? []);
  const warnings = new Set(result?.warnings ?? []);
  return REVIEWER_CHECKS.map(({ code, label }) => ({
    code,
    label,
    state: failures.has(code) ? "fail" : warnings.has(code) ? "warn" : "pass",
  }));
}

const ICON = { pass: CheckCircle2, fail: XCircle, warn: AlertTriangle } as const;
const TEXT = { pass: "Pass", fail: "Fail", warn: "Warn" } as const;

/** The reviewer's pure-code checks (arch §8.6), failures first. */
export function ReviewerChecklist({ result }: { result: ReviewerResult | undefined }) {
  const states = checkStates(result);
  const order: Record<State, number> = { fail: 0, warn: 1, pass: 2 };
  const sorted = [...states].sort((a, b) => order[a.state] - order[b.state]);
  const failed = states.filter((s) => s.state === "fail").length;
  return (
    <div>
      <p className="mb-2 text-body-sm text-ink-muted" aria-live="polite">
        {failed === 0 ? `All ${states.length} checks passed` : `${failed} failed · ${states.length - failed} passed`}
      </p>
      <ul className="grid grid-cols-1 gap-1 sm:grid-cols-2">
        {sorted.map(({ code, label, state }) => {
          const Icon = ICON[state];
          return (
            <li
              key={code}
              className={cx(
                "flex items-center gap-2 rounded border px-2 py-1 text-body-sm",
                state === "fail" && "border-critical-line bg-critical-bg text-critical-fg",
                state === "warn" && "border-warning-line bg-warning-bg text-warning-fg",
                state === "pass" && "border-transparent text-ink-muted",
              )}
            >
              <Icon aria-hidden className={cx("h-4 w-4 shrink-0", state === "pass" && "text-success-solid")} />
              <span className={cx("flex-1", state === "fail" && "font-mono font-medium")}>{state === "fail" ? code : label}</span>
              <span className="font-mono text-label-sm uppercase">{TEXT[state]}</span>
            </li>
          );
        })}
      </ul>
    </div>
  );
}
