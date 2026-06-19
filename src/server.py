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
    MCP_HOST: str = "127.0.0.1"
    MCP_PORT: int = 8010


settings = Settings()

_GH_API = "https://api.github.com"

mcp = FastMCP(
    "agora-mcp-github",
    instructions=(
        "GitHub tools for aGorA agents. Provides read-only access to workflow "
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


def _assert_allowed_org(owner: str) -> None:
    if settings.ALLOWED_ORG and owner != settings.ALLOWED_ORG:
        raise PermissionError(f"Repo owner '{owner}' is not in the allowed org '{settings.ALLOWED_ORG}'")


@mcp.tool()
async def get_workflow_yaml(owner: str, repo: str, path: str, ref: str) -> str:
    """Fetch a GitHub Actions workflow YAML file from a repository."""
    _assert_allowed_org(owner)
    token = _mint_installation_token(owner)
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
async def get_run_logs(owner: str, repo: str, run_id: int) -> str:
    """Download and return the last 300 lines of logs for a workflow run."""
    _assert_allowed_org(owner)
    import io, zipfile
    token = _mint_installation_token(owner)
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
async def create_fix_branch(owner: str, repo: str, base_sha: str, branch_name: str) -> str:
    """Create a new fix branch from a given commit SHA. Branch name must start with 'agora/'."""
    _assert_allowed_org(owner)
    if not branch_name.startswith("agora/"):
        raise ValueError("Fix branches must be prefixed 'agora/' — rejecting arbitrary branch creation")
    token = _mint_installation_token(owner)
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
) -> str:
    """Commit a fixed workflow YAML. Path must be inside .github/workflows/."""
    _assert_allowed_org(owner)
    if not workflow_path.startswith(".github/workflows/"):
        raise ValueError("commit_workflow_fix only writes to .github/workflows/ — rejecting arbitrary path")
    if not branch.startswith("agora/"):
        raise ValueError("Commits must target an 'agora/' branch")
    token = _mint_installation_token(owner)
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
) -> str:
    """Open a pull request. Head branch must start with 'agora/'."""
    _assert_allowed_org(owner)
    if not head.startswith("agora/"):
        raise ValueError("PR head branch must start with 'agora/'")
    token = _mint_installation_token(owner)
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
    mcp.run(transport="stdio")
