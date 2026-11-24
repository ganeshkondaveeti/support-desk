import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

describe('local MCP backend', () => {
  let tmpDir: string;
  let docPath: string;
  let emailPath: string;

  beforeEach(() => {
    vi.resetModules();
    tmpDir = fs.mkdtempSync(path.join(os.tmpdir(), 'mcp-local-'));
    docPath = path.join(tmpDir, 'appended_doc_entry.md');
    emailPath = path.join(tmpDir, 'email_draft.md');
    process.env.MCP_BACKEND = 'local';
    process.env.LOCAL_DOC_PATH = docPath;
    process.env.LOCAL_EMAIL_PATH = emailPath;
    process.env.GOOGLE_CLIENT_ID ??= 'test-client-id';
    process.env.GOOGLE_CLIENT_SECRET ??= 'test-client-secret';
    process.env.GOOGLE_REDIRECT_URI ??= 'http://localhost:3000/oauth/callback';
  });

  afterEach(() => {
    fs.rmSync(tmpDir, { recursive: true, force: true });
    delete process.env.MCP_BACKEND;
  });

  it('appends once and de-duplicates on action marker', async () => {
    const { appendDocLocal } = await import('../src/services/localFileService.js');
    const first = appendDocLocal('## Pulse\n\nhello', 'a_1');
    expect(first.deduplicated).toBe(false);
    const text = fs.readFileSync(docPath, 'utf8');
    expect(text).toContain('<!-- action:a_1 -->');
    expect(text).toContain('hello');

    const second = appendDocLocal('## Pulse\n\nhello again', 'a_1');
    expect(second.deduplicated).toBe(true);
    const after = fs.readFileSync(docPath, 'utf8');
    expect(after.match(/hello/g)?.length).toBe(1);
  });

  it('writes email draft md and eml, de-duplicates', async () => {
    const { writeEmailDraftLocal } = await import('../src/services/localFileService.js');
    const first = writeEmailDraftLocal(
      { to: ['product@example.com'], subject: 'Pulse', body: 'Body text' },
      'a_email',
    );
    expect(first.deduplicated).toBe(false);
    expect(fs.existsSync(emailPath)).toBe(true);
    expect(fs.existsSync(emailPath.replace(/\.md$/, '.eml'))).toBe(true);
    expect(fs.readFileSync(emailPath, 'utf8')).toContain('<!-- action:a_email -->');
    expect(fs.readFileSync(emailPath.replace(/\.md$/, '.eml'), 'utf8')).toContain(
      'X-Support-Desk-Action-Id: a_email',
    );

    const second = writeEmailDraftLocal(
      { to: [], subject: 'Pulse', body: 'Body text 2' },
      'a_email',
    );
    expect(second.deduplicated).toBe(true);
  });
});
