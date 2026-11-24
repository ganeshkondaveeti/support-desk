import { AlertTriangle, ExternalLink } from "lucide-react";

import { cx } from "../lib/cx";
import { useLinkPolicy } from "../lib/policy";
import { isAllowedUrl, urlLabel } from "../lib/safe";

/**
 * A citation is a link only when the URL came from the API and is on the public
 * allow-list (plan P9.4, audit S2). Anything else renders as plain text with a warning.
 */
export function CitationLink({ url, label, className }: { url: string | null | undefined; label?: string; className?: string }) {
  const policy = useLinkPolicy();
  if (!url) return null;
  const text = label ?? urlLabel(url);
  if (!isAllowedUrl(url, policy)) {
    return (
      <span className={cx("inline-flex items-center gap-1 text-body-sm text-warning-fg", className)} title="Not on the public source allow-list">
        <AlertTriangle aria-hidden className="h-3.5 w-3.5" />
        <span className="break-all">{text}</span>
      </span>
    );
  }
  return (
    <a
      href={url}
      target="_blank"
      rel="noopener noreferrer"
      className={cx("inline-flex items-center gap-1 break-all text-body-sm font-medium text-primary hover:text-primary-hover hover:underline", className)}
    >
      {text}
      <ExternalLink aria-hidden className="h-3.5 w-3.5 shrink-0" />
      <span className="sr-only">(opens in a new tab)</span>
    </a>
  );
}
