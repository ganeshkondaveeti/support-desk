# The Support Desk

Facts-only. No investment advice.

Groww support + product loop over six HDFC Mutual Fund schemes: weekly pulse from Play Store reviews, cited reply drafts from a three-agent LangGraph desk, and a human approval gate before every write or send.

See [docs/architecture.md](docs/architecture.md) and [docs/problemStatement.md](docs/problemStatement.md).

## Quick start (P0)

```bash
cp .env.example .env
uv sync
make test
make lint
make dev   # http://127.0.0.1:8000/api/health → {"status":"ok"}
```

MCP server (Node ≥ 22):

```bash
cd mcp-server && npm ci && npm test && npm run build
```

Full setup, UI, evals, and the hosted URL land in later phases.
