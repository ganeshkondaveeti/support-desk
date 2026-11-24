import { GoogleDocsAppendSchema } from '../../schemas/googleDocsSchemas.js';
import { config } from '../../config.js';
import { googleDocsService } from '../../services/googleDocsService.js';
import { appendDocLocal } from '../../services/localFileService.js';
import { MCPToolResponse } from '../../types/common.js';
import { MCPToolError, ErrorCode } from '../../utils/errors.js';

export const googleDocsAppendTool = {
  name: 'google_docs_append',
  description: "Appends text content to the END of an existing Google Document. "
    + "Existing content is preserved. A newline separator is inserted before the appended text. "
    + "Requires the document ID (found in the Google Docs URL). "
    + "Optional idempotency_key de-duplicates retries (named range action:{key}).",
  inputSchema: {
    type: "object",
    properties: {
      document_id: { type: "string", description: "The ID of the Google Document (found in URL)" },
      content: { type: "string", description: "The text content to append to the document" },
      idempotency_key: { type: "string", description: "Optional key used to skip duplicate appends" }
    },
    required: ["document_id", "content"]
  },
};

export async function handleGoogleDocsAppend(args: any): Promise<MCPToolResponse<any>> {
  try {
    const parsed = GoogleDocsAppendSchema.safeParse(args);
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
      const result = appendDocLocal(parsed.data.content, parsed.data.idempotency_key);
      return {
        success: true,
        data: result,
        message: result.deduplicated ? 'Content already appended (de-duplicated)' : 'Content appended locally',
      };
    }

    const result = await googleDocsService.appendContent(parsed.data);

    return {
      success: true,
      data: result,
      message: result.deduplicated ? 'Content already appended (de-duplicated)' : 'Content appended successfully',
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
