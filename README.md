# stagecraft-mcp

MCP (Model Context Protocol) server that gives [StageCraft](https://github.com/StagecraftOps)'s agents structured, guard-railed access to GitHub. Instead of letting agents shell out or craft arbitrary API calls, the worker's LangGraph agents call these eight tools over SSE.

**Port**: 8010 · **Stack**: FastMCP, httpx, PyJWT · **Package**: `stagecraft-mcp` (Python ≥3.12)

## Tools exposed

| Tool | What it does |
|---|---|
| `get_workflow_yaml` | Fetch a workflow file's content at a ref |
| `get_run_logs` | Download a workflow run's logs |
| `get_pull_request_diff` | Fetch a PR's diff for review agents |
| `search_remediations` | Semantic search over past remediations (via the StageCraft API) |
| `query_graph` | Query workflow dependency relationships |
| `create_fix_branch` | Create a branch from a base SHA |
| `commit_workflow_fix` | Commit a fixed workflow file to that branch |
| `create_pull_request` | Open the PR |

## Guardrails

- **Org allowlist** — every tool call is checked against `ALLOWED_ORG`; requests for any other owner are refused
- **Branch prefix** — write tools only operate on `stagecraft/`-prefixed branches, so the agent can never push to `main` or an existing feature branch
- **Auth** — mints short-lived GitHub App installation tokens (`GITHUB_APP_ID` + `GITHUB_APP_PRIVATE_KEY`) per call; callers may pass a scoped token instead

## What it needs

| Variable | Why |
|---|---|
| `GITHUB_APP_ID`, `GITHUB_APP_PRIVATE_KEY` | Mint installation tokens for GitHub calls |
| `ALLOWED_ORG` | The single GitHub org tools may touch |
| `STAGECRAFT_API_URL`, `INTERNAL_API_KEY` | For `search_remediations`/`query_graph`, which go through the API |
| `MCP_HOST` / `MCP_PORT` | Bind address (defaults `0.0.0.0:8010`) |

## Run

```bash
pip install -e .
python src/server.py        # or: docker build -t stagecraft-mcp . && docker run -p 8010:8010 stagecraft-mcp
# SSE endpoint at http://localhost:8010/sse
```

In-cluster it's a ClusterIP service only — called by [stagecraft-worker](https://github.com/StagecraftOps/stagecraft-worker) over SSE (`USE_MCP_TOOLS=true`), never exposed externally. Deployed by [stagecraft-helm](https://github.com/StagecraftOps/stagecraft-helm)'s `mcp` chart.
