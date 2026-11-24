import type { ReactNode } from "react";

import { RedactedText } from "./RedactedText";

/**
 * Renders the pulse's Markdown as structured text: headings, numbered items, bullets
 * and quotes. Every value is a text node; no HTML is ever parsed or injected (plan P9.4).
 */
export function PlainMarkdown({ source }: { source: string }) {
  const out: ReactNode[] = [];
  source.split("\n").forEach((raw, i) => {
    const line = raw.trimEnd();
    if (!line.trim()) return;
    let m: RegExpExecArray | null;
    if ((m = /^#\s+(.*)$/.exec(line))) {
      out.push(
        <h3 key={i} className="text-headline-md text-ink">
          {m[1]}
        </h3>,
      );
    } else if ((m = /^##\s+(.*)$/.exec(line))) {
      out.push(
        <h4 key={i} className="eyebrow pt-3">
          {m[1]}
        </h4>,
      );
    } else if ((m = /^\s*>\s?(.*)$/.exec(line))) {
      out.push(
        <blockquote key={i} className="ml-4 border-l-2 border-primary-line pl-3 text-body-md italic text-ink-muted">
          <RedactedText text={m[1] ?? ""} />
        </blockquote>,
      );
    } else if ((m = /^\s*(\d+)\.\s+(.*)$/.exec(line))) {
      out.push(
        <p key={i} className="flex gap-2 text-body-md text-ink">
          <span className="font-mono text-label-md text-ink-faint">{m[1]}.</span>
          <span>
            <RedactedText text={m[2] ?? ""} />
          </span>
        </p>,
      );
    } else if ((m = /^\s*-\s+(.*)$/.exec(line))) {
      out.push(
        <p key={i} className="flex gap-2 text-body-md text-ink">
          <span aria-hidden className="text-ink-faint">•</span>
          <span>
            <RedactedText text={m[1] ?? ""} />
          </span>
        </p>,
      );
    } else {
      out.push(
        <p key={i} className="text-body-md text-ink">
          <RedactedText text={line} />
        </p>,
      );
    }
  });
  return <div className="space-y-1.5">{out}</div>;
}
