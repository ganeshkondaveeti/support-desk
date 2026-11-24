import dotenv from 'dotenv';
import { z } from 'zod';
import path from 'node:path';

// Load environment variables from .env file. Quiet: under the stdio transport, stdout
// carries the MCP protocol, so nothing else may print there.
dotenv.config({ quiet: true });

const backend = (process.env.MCP_BACKEND ?? 'google').toLowerCase();
const isLocal = backend === 'local';

const googleRequired = z.string().min(1);
const googleOptionalWhenLocal = isLocal
  ? z.string().optional().default('local-unused')
  : googleRequired;

const configSchema = z.object({
  MCP_BACKEND: z.enum(['local', 'google']).default('google'),
  LOCAL_DOC_PATH: z.string().default('samples/appended_doc_entry.md'),
  LOCAL_EMAIL_PATH: z.string().default('samples/email_draft.md'),
  GOOGLE_CLIENT_ID: googleOptionalWhenLocal,
  GOOGLE_CLIENT_SECRET: googleOptionalWhenLocal,
  GOOGLE_REDIRECT_URI: googleOptionalWhenLocal,
  GOOGLE_TOKEN_PATH: z.string().default('./data/google-token.json'),
  GOOGLE_TOKEN_JSON: z.string().optional(),
  PORT: z.string().default('3000'),
  LOG_LEVEL: z.enum(['debug', 'info', 'warn', 'error']).default('info'),
  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
});

const parsedConfig = configSchema.safeParse({
  ...process.env,
  MCP_BACKEND: isLocal ? 'local' : 'google',
});

if (!parsedConfig.success) {
  console.error('Invalid environment variables:', JSON.stringify(parsedConfig.error.format(), null, 2));
  process.exit(1);
}

const resolvePath = (p: string) => (path.isAbsolute(p) ? p : path.resolve(process.cwd(), p));

export const config = {
  backend: parsedConfig.data.MCP_BACKEND as 'local' | 'google',
  local: {
    docPath: resolvePath(parsedConfig.data.LOCAL_DOC_PATH),
    emailPath: resolvePath(parsedConfig.data.LOCAL_EMAIL_PATH),
  },
  google: {
    clientId: parsedConfig.data.GOOGLE_CLIENT_ID ?? 'local-unused',
    clientSecret: parsedConfig.data.GOOGLE_CLIENT_SECRET ?? 'local-unused',
    redirectUri: parsedConfig.data.GOOGLE_REDIRECT_URI ?? 'http://localhost/unused',
    tokenPath: parsedConfig.data.GOOGLE_TOKEN_PATH,
    scopes: [
      'https://www.googleapis.com/auth/gmail.compose',
      'https://www.googleapis.com/auth/documents',
    ],
  },
  logging: {
    level: parsedConfig.data.LOG_LEVEL,
  },
  nodeEnv: parsedConfig.data.NODE_ENV,
};
