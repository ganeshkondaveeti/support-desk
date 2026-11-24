import { describe, it, expect, vi, beforeEach } from 'vitest';

process.env.MCP_BACKEND = 'google';
process.env.GOOGLE_CLIENT_ID ??= 'test-client-id';
process.env.GOOGLE_CLIENT_SECRET ??= 'test-client-secret';
process.env.GOOGLE_REDIRECT_URI ??= 'http://localhost:3000/oauth/callback';

const docsGet = vi.fn();
const docsBatchUpdate = vi.fn();
const draftsList = vi.fn();
const draftsGet = vi.fn();
const draftsCreate = vi.fn();

vi.mock('../src/services/googleAuthService.js', () => ({
  authService: {
    getAuthClient: vi.fn(async () => ({})),
  },
}));

vi.mock('googleapis', () => ({
  google: {
    docs: () => ({
      documents: {
        get: docsGet,
        batchUpdate: docsBatchUpdate,
      },
    }),
    gmail: () => ({
      users: {
        drafts: {
          list: draftsList,
          get: draftsGet,
          create: draftsCreate,
        },
      },
    }),
  },
}));

describe('Google backend idempotency (G7)', () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('skips Docs append when named range action:{key} exists', async () => {
    docsGet.mockResolvedValue({
      data: {
        body: { content: [{ endIndex: 10 }] },
        namedRanges: {
          'action:a_dup': { nameRanges: [{ name: 'action:a_dup' }] },
        },
      },
    });

    const { googleDocsService } = await import('../src/services/googleDocsService.js');
    const result = await googleDocsService.appendContent({
      document_id: 'doc1',
      content: 'hello',
      idempotency_key: 'a_dup',
    });
    expect(result?.deduplicated).toBe(true);
    expect(docsBatchUpdate).not.toHaveBeenCalled();
  });

  it('creates named range on Docs append', async () => {
    docsGet.mockResolvedValue({
      data: {
        body: { content: [{ endIndex: 10 }] },
        namedRanges: {},
      },
    });
    docsBatchUpdate.mockResolvedValue({ data: {} });

    const { googleDocsService } = await import('../src/services/googleDocsService.js');
    const result = await googleDocsService.appendContent({
      document_id: 'doc1',
      content: 'hello',
      idempotency_key: 'a_new',
    });
    expect(result?.deduplicated).toBe(false);
    expect(docsBatchUpdate).toHaveBeenCalledOnce();
    const requests = docsBatchUpdate.mock.calls[0][0].requestBody.requests;
    expect(requests[0].insertText).toBeTruthy();
    expect(requests[1].createNamedRange.name).toBe('action:a_new');
  });

  it('skips Gmail draft when X-Support-Desk-Action-Id matches', async () => {
    draftsList.mockResolvedValue({ data: { drafts: [{ id: 'd1' }] } });
    draftsGet.mockResolvedValue({
      data: {
        id: 'd1',
        message: {
          id: 'm1',
          payload: {
            headers: [{ name: 'X-Support-Desk-Action-Id', value: 'a_mail' }],
          },
        },
      },
    });

    const { gmailService } = await import('../src/services/gmailService.js');
    const result = await gmailService.createDraft({
      to: [],
      cc: [],
      bcc: [],
      subject: 'Hi',
      body: 'Body',
      idempotency_key: 'a_mail',
    });
    expect(result?.deduplicated).toBe(true);
    expect(draftsCreate).not.toHaveBeenCalled();
  });

  it('sets X-Support-Desk-Action-Id when creating a draft', async () => {
    draftsList.mockResolvedValue({ data: { drafts: [] } });
    draftsCreate.mockResolvedValue({ data: { id: 'd2', message: { id: 'm2' } } });

    const { gmailService } = await import('../src/services/gmailService.js');
    const result = await gmailService.createDraft({
      to: ['a@b.com'],
      cc: [],
      bcc: [],
      subject: 'Hi',
      body: 'Body',
      idempotency_key: 'a_mail2',
    });
    expect(result?.deduplicated).toBe(false);
    expect(draftsCreate).toHaveBeenCalledOnce();
    const raw = draftsCreate.mock.calls[0][0].requestBody.message.raw as string;
    const mime = Buffer.from(raw, 'base64url').toString('utf8');
    expect(mime).toContain('X-Support-Desk-Action-Id: a_mail2');
  });
});
