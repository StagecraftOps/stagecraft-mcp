import base64
import time
from typing import Optional

import httpx
import jwt
from fastmcp import FastMCP
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    GITHUB_APP_ID: str = ""
    GITHUB_APP_PRIVATE_KEY: str = ""
    ALLOWED_ORG: str = ""
    MCP_HOST: str = "0.0.0.0"
    MCP_PORT: int = 8010

    STAGECRAFT_API_URL: str = "http://stagecraft-api.stagecraft.svc.cluster.local:8000"
    INTERNAL_API_KEY: str = ""

settings = Settings()

_GH_API = "https://api.github.com"

mcp = FastMCP(
    "stagecraft-mcp",
    instructions=(
        "GitHub tools for Stagecraft agents. Provides read-only access to workflow "
        "YAMLs and run logs, and write-only access to create fix branches, commit "
        "patched files, and open pull requests on pre-approved repositories."
    ),
)

def _mint_installation_token(org: str) -> str:
    now = int(time.time())
    payload = {"iat": now - 60, "exp": now + 600, "iss": settings.GITHUB_APP_ID}
    pem = settings.GITHUB_APP_PRIVATE_KEY.replace("\\n", "\n")
    app_jwt = jwt.encode(payload, pem, algorithm="RS256")

    with httpx.Client() as client:
        r = client.get(
            f"{_GH_API}/orgs/{org}/installation",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
        )
        r.raise_for_status()
        installation_id = r.json()["id"]

        r2 = client.post(
            f"{_GH_API}/app/installations/{installation_id}/access_tokens",
            headers={"Authorization": f"Bearer {app_jwt}", "Accept": "application/vnd.github+json"},
            json={"permissions": {"contents": "write", "pull_requests": "write"}},
        )
        r2.raise_for_status()
        return r2.json()["token"]

def _resolve_token(org: str, github_token: Optional[str]) -> str:
    if github_token:
        return github_token
    if not settings.GITHUB_APP_ID or not settings.GITHUB_APP_PRIVATE_KEY:
        raise RuntimeError(
            "No github_token provided and GITHUB_APP_ID/GITHUB_APP_PRIVATE_KEY are not configured. "
            "Pass a github_token to use OAuth auth, or configure the GitHub App."
        )
    return _mint_installation_token(org)

def _assert_allowed_org(owner: str) -> None:
    if settings.ALLOWED_ORG and owner != settings.ALLOWED_ORG:
        raise PermissionError(f"Repo owner '{owner}' is not in the allowed org '{settings.ALLOWED_ORG}'")

@mcp.tool()
async def get_pull_request_diff(owner: str, repo: str, pr_number: int, github_token: Optional[str] = None) -> str:
    _assert_allowed_org(owner)
    token = _resolve_token(owner, github_token)
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_GH_API}/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.diff",
            },
        )
        r.raise_for_status()
        return r.text

@mcp.tool()
async def get_workflow_yaml(owner: str, repo: str, path: str, ref: str, github_token: Optional[str] = None) -> str:
    _assert_allowed_org(owner)
    token = _resolve_token(owner, github_token)
    async with httpx.AsyncClient() as client:
        r = await client.get(
            f"{_GH_API}/repos/{owner}/{repo}/contents/{path}",
            params={"ref": ref},
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github.raw+json",
            },
        )
        r.raise_for_status()
        return r.text

@mcp.tool()
async def get_run_logs(owner: str, repo: str, run_id: int, github_token: Optional[str] = None) -> str:
    _assert_allowed_org(owner)
    import io, zipfile
    token = _resolve_token(owner, github_token)
    async with httpx.AsyncClient(follow_redirects=True) as client:
        r = await client.get(
            f"{_GH_API}/repos/{owner}/{repo}/actions/runs/{run_id}/logs",
            headers={"Authorization": f"Bearer {token}"},
        )
        r.raise_for_status()
        zf = zipfile.ZipFile(io.BytesIO(r.content))
        lines = []
        for name in sorted(zf.namelist()):
            if name.endswith(".txt"):
                lines.extend(zf.read(name).decode("utf-8", errors="replace").splitlines())
        return "\n".join(lines[-300:])

@mcp.tool()
async def search_remediations(
    query: Optional[str] = None,
    repo_name: Optional[str] = None,
    failure_category: Optional[str] = None,
    since_days: Optional[int] = None,
    limit: int = 8,
) -> str:
    if not settings.INTERNAL_API_KEY:
        raise RuntimeError("INTERNAL_API_KEY is not configured — cannot call stagecraft-api/internal")
    payload = {
        "query": query,
        "repo_name": repo_name,
        "failure_category": failure_category,
        "since_days": since_days,
        "limit": limit,
    }
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{settings.STAGECRAFT_API_URL}/internal/remediations/search",
            json=payload,
            headers={"X-Internal-Api-Key": settings.INTERNAL_API_KEY},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.text

@mcp.tool()
async def query_graph(workflow_file: str, repo_name: str | None = None, relationship: str = "depends_on") -> str:
    if not settings.INTERNAL_API_KEY:
        raise RuntimeError("INTERNAL_API_KEY is not configured — cannot call stagecraft-api/internal")
    payload = {"repo_name": repo_name, "workflow_file": workflow_file, "relationship": relationship}
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{settings.STAGECRAFT_API_URL}/internal/graph/query",
            json=payload,
            headers={"X-Internal-Api-Key": settings.INTERNAL_API_KEY},
            timeout=30.0,
        )
        r.raise_for_status()
        return r.text

@mcp.tool()
async def create_fix_branch(owner: str, repo: str, base_sha: str, branch_name: str, github_token: Optional[str] = None) -> str:
    _assert_allowed_org(owner)
    if not branch_name.startswith("stagecraft/"):
        raise ValueError("Fix branches must be prefixed 'stagecraft/' — rejecting arbitrary branch creation")
    token = _resolve_token(owner, github_token)
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{_GH_API}/repos/{owner}/{repo}/git/refs",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"ref": f"refs/heads/{branch_name}", "sha": base_sha},
        )
        r.raise_for_status()
        return f"Branch '{branch_name}' created at {base_sha}"

@mcp.tool()
async def commit_workflow_fix(
    owner: str,
    repo: str,
    branch: str,
    workflow_path: str,
    content: str,
    message: str,
    current_sha: Optional[str] = None,
    github_token: Optional[str] = None,
) -> str:
    _assert_allowed_org(owner)
    if not workflow_path.startswith(".github/workflows/"):
        raise ValueError("commit_workflow_fix only writes to .github/workflows/ — rejecting arbitrary path")
    if not branch.startswith("stagecraft/"):
        raise ValueError("Commits must target a 'stagecraft/' branch")
    token = _resolve_token(owner, github_token)
    encoded = base64.b64encode(content.encode()).decode()
    payload = {"message": message, "content": encoded, "branch": branch}
    if current_sha:
        payload["sha"] = current_sha
    async with httpx.AsyncClient() as client:
        r = await client.put(
            f"{_GH_API}/repos/{owner}/{repo}/contents/{workflow_path}",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json=payload,
        )
        r.raise_for_status()
        return f"Committed to {workflow_path} on {branch}"

@mcp.tool()
async def create_pull_request(
    owner: str,
    repo: str,
    head: str,
    base: str,
    title: str,
    body: str,
    github_token: Optional[str] = None,
) -> str:
    _assert_allowed_org(owner)
    if not head.startswith("stagecraft/"):
        raise ValueError("PR head branch must start with 'stagecraft/'")
    token = _resolve_token(owner, github_token)
    async with httpx.AsyncClient() as client:
        r = await client.post(
            f"{_GH_API}/repos/{owner}/{repo}/pulls",
            headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            json={"title": title, "body": body, "head": head, "base": base, "maintainer_can_modify": True},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("html_url", "")

if __name__ == "__main__":
    mcp.run(transport="sse", host=settings.MCP_HOST, port=settings.MCP_PORT)
