import { GmailDraftInputSchema } from '../../schemas/gmailSchemas.js';
import { config } from '../../config.js';
import { gmailService } from '../../services/gmailService.js';
import { writeEmailDraftLocal } from '../../services/localFileService.js';
import { MCPToolResponse } from '../../types/common.js';
import { MCPToolError, ErrorCode } from '../../utils/errors.js';

export const gmailDraftTool = {
  name: 'gmail_draft',
  description: "Creates an email draft in the authenticated user's Gmail account. "
    + "It does NOT send the email, and this server has no send tool: a person sends "
    + "the draft from Gmail. Recipients are optional. "
    + "Optional idempotency_key de-duplicates via X-Support-Desk-Action-Id.",
  inputSchema: {
    type: "object",
    properties: {
      to: { type: "array", items: { type: "string", format: "email" }, description: "Recipient email addresses; may be empty" },
      cc: { type: "array", items: { type: "string", format: "email" }, description: "Array of CC email addresses" },
      bcc: { type: "array", items: { type: "string", format: "email" }, description: "Array of BCC email addresses" },
      subject: { type: "string", description: "Email subject" },
      body: { type: "string", description: "Email body text" },
      idempotency_key: { type: "string", description: "Optional key used to skip duplicate drafts" }
    },
    required: ["subject", "body"]
  },
};

export async function handleGmailDraft(args: any): Promise<MCPToolResponse<any>> {
  try {
    const parsed = GmailDraftInputSchema.safeParse(args);
    if (!parsed.success) {
      return {
        success: false,
        error: {
          code: ErrorCode.MISSING_REQUIRED_FIELD,
          message: `Validation failed: ${parsed.error.message}`,
          retryable: false,
        },
      };
    }

    if (config.backend === 'local') {
      const result = writeEmailDraftLocal(parsed.data, parsed.data.idempotency_key);
      return {
        success: true,
        data: result,
        message: result.deduplicated ? 'Draft already exists (de-duplicated)' : 'Email draft written locally',
      };
    }

    const result = await gmailService.createDraft(parsed.data);

    return {
      success: true,
      data: result,
      message: result.deduplicated ? 'Draft already exists (de-duplicated)' : 'Email draft created successfully',
    };
  } catch (error: any) {
    if (error instanceof MCPToolError) {
      return {
        success: false,
        error: {
          code: error.code,
          message: error.message,
          retryable: error.retryable,
        },
      };
    }
    return {
      success: false,
      error: {
        code: ErrorCode.INTERNAL_ERROR,
        message: 'An unexpected error occurred.',
        retryable: false,
      },
    };
  }
}
