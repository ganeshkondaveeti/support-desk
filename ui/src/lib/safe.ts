// Grounding-safe rendering helpers (plan P9.4). Pure functions, unit tested.

export interface LinkPolicy {
  publicDomains: readonly string[];
  eduUrls: readonly string[];
}

/** Mirrors app/guardrails/allowlists.is_public_url: https, host on PUBLIC_DOMAINS or an exact EDU_URL. */
export function isAllowedUrl(url: string | null | undefined, policy: LinkPolicy): boolean {
  if (!url) return false;
  if (policy.eduUrls.includes(url)) return true;
  let parsed: URL;
  try {
    parsed = new URL(url);
  } catch {
    return false;
  }
  if (parsed.protocol !== "https:") return false;
  const host = parsed.hostname.toLowerCase();
  return policy.publicDomains.some((domain) => host === domain || host.endsWith(`.${domain}`));
}

/** Short label for a citation, e.g. "groww.in · hdfc-large-cap-fund-direct-growth". */
export function urlLabel(url: string): string {
  try {
    const parsed = new URL(url);
    const host = parsed.hostname.replace(/^www\./, "");
    const last = parsed.pathname.split("/").filter(Boolean).pop();
    return last ? `${host} · ${last}` : host;
  } catch {
    return url;
  }
}

export const REDACTION_TOKENS = ["EMAIL", "PAN", "AADHAAR", "PHONE", "OTP", "ACCOUNT", "UPI", "IFSC"] as const;
const TOKEN_RE = new RegExp(`\\[(${REDACTION_TOKENS.join("|")})\\]`, "g");

export type TextPart = { kind: "text"; value: string } | { kind: "token"; value: string };

/** Split already-redacted text into plain text and redaction tokens, so tokens render as chips. */
export function splitRedactions(text: string): TextPart[] {
  const parts: TextPart[] = [];
  let last = 0;
  for (const match of text.matchAll(TOKEN_RE)) {
    const start = match.index ?? 0;
    if (start > last) parts.push({ kind: "text", value: text.slice(last, start) });
    parts.push({ kind: "token", value: match[1] ?? "" });
    last = start + match[0].length;
  }
  if (last < text.length) parts.push({ kind: "text", value: text.slice(last) });
  return parts;
}

/** The API shows hex digests with a dash every 3 chars (app/ids.py) so PII scans can't misfire. */
export function normalizeHex(value: string): string {
  return value.replace(/-/g, "").toLowerCase();
}

export async function sha256Hex(text: string): Promise<string> {
  const bytes = new TextEncoder().encode(text);
  const digest = await crypto.subtle.digest("SHA-256", bytes);
  return Array.from(new Uint8Array(digest), (b) => b.toString(16).padStart(2, "0")).join("");
}

/** True when the payload shown is exactly the payload the server froze (arch §10.3). */
export async function payloadMatchesHash(payload: string, serverHash: string): Promise<boolean> {
  return (await sha256Hex(payload)) === normalizeHex(serverHash);
}
