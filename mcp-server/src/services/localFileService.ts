import fs from 'node:fs';
import path from 'node:path';
import { config } from '../config.js';
import { logger } from '../utils/logging.js';

export type LocalAppendResult = {
  path: string;
  deduplicated: boolean;
};

export type LocalDraftResult = {
  path: string;
  emlPath: string;
  deduplicated: boolean;
};

function markerLine(key: string): string {
  return `<!-- action:${key} -->`;
}

function ensureParent(filePath: string): void {
  fs.mkdirSync(path.dirname(filePath), { recursive: true });
}

function fileContainsMarker(filePath: string, key: string): boolean {
  if (!fs.existsSync(filePath)) {
    return false;
  }
  const text = fs.readFileSync(filePath, 'utf8');
  return text.includes(markerLine(key));
}

/**
 * Append to samples/appended_doc_entry.md (or LOCAL_DOC_PATH), de-duplicating
 * on a `<!-- action:{key} -->` line (architecture §10.3 / §10.4).
 */
export function appendDocLocal(content: string, idempotencyKey?: string): LocalAppendResult {
  const filePath = config.local.docPath;
  ensureParent(filePath);

  if (idempotencyKey && fileContainsMarker(filePath, idempotencyKey)) {
    logger.info({ path: filePath, idempotencyKey }, 'Local doc append de-duplicated');
    return { path: filePath, deduplicated: true };
  }

  const marker = idempotencyKey ? `${markerLine(idempotencyKey)}\n` : '';
  const block = `\n\n${marker}${content.trimEnd()}\n`;
  fs.appendFileSync(filePath, block, 'utf8');
  logger.info({ path: filePath, idempotencyKey }, 'Local doc append written');
  return { path: filePath, deduplicated: false };
}

/**
 * Write samples/email_draft.md (+ .eml copy), de-duplicating on the marker line.
 */
export function writeEmailDraftLocal(
  input: { to: string[]; subject: string; body: string; cc?: string[]; bcc?: string[] },
  idempotencyKey?: string,
): LocalDraftResult {
  const filePath = config.local.emailPath;
  const emlPath = filePath.replace(/\.md$/i, '.eml');
  ensureParent(filePath);

  if (idempotencyKey && fileContainsMarker(filePath, idempotencyKey)) {
    logger.info({ path: filePath, idempotencyKey }, 'Local email draft de-duplicated');
    return { path: filePath, emlPath, deduplicated: true };
  }

  const marker = idempotencyKey ? `${markerLine(idempotencyKey)}\n` : '';
  const toLine = input.to.length > 0 ? input.to.join(', ') : '(add recipient in Gmail)';
  const md = [
    marker.trimEnd(),
    `# Email draft`,
    ``,
    `- **To:** ${toLine}`,
    input.cc && input.cc.length ? `- **Cc:** ${input.cc.join(', ')}` : null,
    input.bcc && input.bcc.length ? `- **Bcc:** ${input.bcc.join(', ')}` : null,
    `- **Subject:** ${input.subject}`,
    ``,
    input.body.trimEnd(),
    ``,
  ]
    .filter((line) => line !== null)
    .join('\n');

  fs.writeFileSync(filePath, md, 'utf8');

  const emlHeaders = [
    input.to.length ? `To: ${input.to.join(', ')}` : null,
    input.cc && input.cc.length ? `Cc: ${input.cc.join(', ')}` : null,
    input.bcc && input.bcc.length ? `Bcc: ${input.bcc.join(', ')}` : null,
    `Subject: ${input.subject}`,
    idempotencyKey ? `X-Support-Desk-Action-Id: ${idempotencyKey}` : null,
    `Content-Type: text/plain; charset="UTF-8"`,
    ``,
    input.body,
  ]
    .filter((line) => line !== null)
    .join('\r\n');
  fs.writeFileSync(emlPath, emlHeaders, 'utf8');

  logger.info({ path: filePath, emlPath, idempotencyKey }, 'Local email draft written');
  return { path: filePath, emlPath, deduplicated: false };
}
