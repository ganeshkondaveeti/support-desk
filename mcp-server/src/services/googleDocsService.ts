import { google, docs_v1 } from 'googleapis';
import { authService } from './googleAuthService.js';
import { GoogleDocsAppendInput } from '../schemas/googleDocsSchemas.js';
import { MCPToolError, ErrorCode } from '../utils/errors.js';
import { logger } from '../utils/logging.js';

function namedRangeName(key: string): string {
  return `action:${key}`;
}

export class GoogleDocsService {
  private async getDocsApi(): Promise<docs_v1.Docs> {
    try {
      const auth = await authService.getAuthClient();
      return google.docs({ version: 'v1', auth });
    } catch (error: any) {
       if (error instanceof MCPToolError) throw error;
       throw new MCPToolError(ErrorCode.AUTHENTICATION_FAILED, 'Failed to authenticate with Google Docs API');
    }
  }

  public async appendContent(input: GoogleDocsAppendInput) {
    try {
      const docs = await this.getDocsApi();

      logger.info({ documentId: input.document_id }, 'Fetching document to determine end index');

      let document;
      try {
        const response = await docs.documents.get({
          documentId: input.document_id,
        });
        document = response.data;
      } catch (error: any) {
        if (error.response && error.response.status === 404) {
           throw new MCPToolError(ErrorCode.DOCUMENT_NOT_FOUND, `Document with ID ${input.document_id} not found.`);
        }
        throw error;
      }

      if (!document.body || !document.body.content) {
          throw new MCPToolError(ErrorCode.GOOGLE_API_ERROR, "Document body is empty or malformed.");
      }

      // G7: skip if a named range action:{key} already exists.
      // documents.get returns namedRanges keyed by name → { namedRanges: [...] }.
      if (input.idempotency_key) {
        const expected = namedRangeName(input.idempotency_key);
        const ranges = document.namedRanges ?? {};
        const byKey = ranges[expected];
        const existsElsewhere = Object.values(ranges).some((entry) =>
          (entry.namedRanges ?? []).some((nr) => nr.name === expected),
        );
        if (byKey || existsElsewhere) {
          logger.info({ documentId: input.document_id, key: expected }, 'Docs append de-duplicated via named range');
          return {
            documentId: input.document_id,
            deduplicated: true,
          };
        }
      }

      // Determine the end index of the document
      const lastElement = document.body.content[document.body.content.length - 1];
      const endIndex = (lastElement.endIndex || 1) - 1;

      // Append content with a leading newline
      const textToInsert = `\n${input.content}`;
      const startIndex = endIndex;
      const endOfInsert = startIndex + textToInsert.length;

      logger.info({ documentId: input.document_id, endIndex }, 'Appending content to document');

      const requests: docs_v1.Schema$Request[] = [
        {
          insertText: {
            location: {
              index: endIndex,
            },
            text: textToInsert,
          },
        },
      ];

      if (input.idempotency_key) {
        requests.push({
          createNamedRange: {
            name: namedRangeName(input.idempotency_key),
            range: {
              startIndex,
              endIndex: endOfInsert,
            },
          },
        });
      }

      await docs.documents.batchUpdate({
        documentId: input.document_id,
        requestBody: { requests },
      });

      return {
        documentId: input.document_id,
        deduplicated: false,
      };
    } catch (error: any) {
      if (error instanceof MCPToolError) throw error;
      this.handleApiError(error, 'document append');
    }
  }

  private handleApiError(error: any, operation: string): never {
    // Status only: a Google API error carries the request, and so the appended text.
    logger.error({ status: error.response?.status }, `Error during ${operation}`);

    if (error.response) {
      const status = error.response.status;
      if (status === 401 || status === 403) {
        throw new MCPToolError(ErrorCode.INSUFFICIENT_PERMISSIONS, `Insufficient permissions for ${operation}. Check OAuth scopes.`, false);
      }
      if (status === 429) {
        throw new MCPToolError(ErrorCode.RATE_LIMITED, `Rate limited by Google Docs API during ${operation}.`, true);
      }
      throw new MCPToolError(ErrorCode.GOOGLE_API_ERROR, `Google Docs API error during ${operation}: ${error.message}`, false);
    }

    throw new MCPToolError(ErrorCode.INTERNAL_ERROR, `Unexpected error during ${operation}`, false);
  }
}

export const googleDocsService = new GoogleDocsService();
