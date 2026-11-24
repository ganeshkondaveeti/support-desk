import { splitRedactions } from "../lib/safe";

/** Renders redacted text with [PAN]/[PHONE]/… as chips. Text only, never HTML. */
export function RedactedText({ text, className }: { text: string; className?: string }) {
  return (
    <span className={className}>
      {splitRedactions(text).map((part, i) =>
        part.kind === "token" ? (
          <span
            key={i}
            title={`${part.value} removed before storage`}
            className="mx-px inline-flex items-center rounded-sm border border-line bg-subtle px-1 font-mono text-label-sm text-ink-muted"
          >
            [{part.value}]
          </span>
        ) : (
          <span key={i}>{part.value}</span>
        ),
      )}
    </span>
  );
}
