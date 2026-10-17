"""
GitHub MCP — lets ChatGPT (or any MCP client) use Abrar's GitHub account (Arety30) through the GitHub REST API.

Tools: whoami, list/search repos, repo info, browse and read files, write a file (one commit), branches and
commits, issues and pull requests (read, create, comment, close/merge), search code, create a repo, delete a
repo, and a read-only `api_get` for anything else. Destructive or public-facing actions need confirm=true.
The token comes from the `gh` CLI login (`gh auth token`) and is never returned.

    python server.py --http [--port 8980]  -> streamable HTTP at /mcp on 127.0.0.1
                                              (published as https://example.com/github/<token>)
"""
from __future__ import annotations

import argparse
import base64
import json
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from mcp.server.fastmcp import FastMCP

MAX_TEXT = 15000
GH = shutil.which("gh") or "/usr/bin/gh"

mcp = FastMCP(
    "github",
    instructions=(
        "Tools for Abrar's GitHub account (Arety30). Read freely. Before anything that deletes, merges, closes, "
        "makes something public, or replaces an existing file, explain what will change, get Abrar's explicit OK, then "
        "call again with confirm=true. New repos are private unless Abrar says otherwise. Never put secrets or tokens "
        "into files, issues or commits."
    ),
)


def _token() -> str:
    return subprocess.run([GH, "auth", "token"], capture_output=True, text=True, timeout=20).stdout.strip()


def _gh(method: str, path: str, body: Any = None, accept: str = "application/vnd.github+json") -> Any:
    url = path if path.startswith("https://") else "https://api.github.com" + path
    req = urllib.request.Request(url, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Authorization": "Bearer " + _token(), "Accept": accept,
                                          "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "arety-github-mcp"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            raw = r.read()
    except urllib.error.HTTPError as e:
        msg = (e.read() or b"").decode(errors="replace")[:500]
        raise RuntimeError(f"GitHub {e.code}: {msg}") from None
    if accept.endswith("diff"):
        return raw.decode(errors="replace")
    return json.loads(raw) if raw else {}


def _clip(s: str) -> str:
    return s if len(s) <= MAX_TEXT else s[:MAX_TEXT] + f"\n… ({len(s) - MAX_TEXT} more characters)"


def _me() -> str:
    return _gh("GET", "/user")["login"]


def _full(repo: str) -> str:
    return repo if "/" in repo else f"{_me()}/{repo}"


@mcp.tool()
def whoami() -> dict:
    """The GitHub account these tools act as."""
    u = _gh("GET", "/user")
    return {"login": u["login"], "name": u.get("name"), "public_repos": u.get("public_repos"), "private_repos": u.get("total_private_repos")}


@mcp.tool()
def list_repos(limit: int = 50) -> list[dict]:
    """Abrar's repositories, most recently pushed first."""
    rs = _gh("GET", f"/user/repos?sort=pushed&per_page={max(1, min(limit, 100))}&affiliation=owner")
    return [{"name": r["full_name"], "private": r["private"], "description": r.get("description"), "pushed": r["pushed_at"],
             "default_branch": r["default_branch"]} for r in rs]


@mcp.tool()
def search_repos(query: str, limit: int = 20) -> list[dict]:
    """Search all of GitHub for repositories."""
    r = _gh("GET", "/search/repositories?q=" + urllib.parse.quote(query) + f"&per_page={max(1, min(limit, 50))}")
    return [{"name": x["full_name"], "stars": x["stargazers_count"], "description": x.get("description")} for x in r.get("items", [])]


@mcp.tool()
def search_code(query: str, repo: str = "", limit: int = 20) -> list[dict]:
    """Search code (optionally inside one repo)."""
    q = query + (f" repo:{_full(repo)}" if repo else "")
    r = _gh("GET", "/search/code?q=" + urllib.parse.quote(q) + f"&per_page={max(1, min(limit, 50))}")
    return [{"repo": x["repository"]["full_name"], "path": x["path"], "url": x["html_url"]} for x in r.get("items", [])]


@mcp.tool()
def list_files(repo: str, path: str = "", ref: str = "") -> list[dict]:
    """Files and folders at a path in a repo."""
    q = f"?ref={urllib.parse.quote(ref)}" if ref else ""
    r = _gh("GET", f"/repos/{_full(repo)}/contents/{urllib.parse.quote(path)}{q}")
    r = r if isinstance(r, list) else [r]
    return [{"path": x["path"], "type": x["type"], "size": x.get("size")} for x in r]


@mcp.tool()
def read_file(repo: str, path: str, ref: str = "") -> str:
    """Read a text file from a repo."""
    q = f"?ref={urllib.parse.quote(ref)}" if ref else ""
    r = _gh("GET", f"/repos/{_full(repo)}/contents/{urllib.parse.quote(path)}{q}")
    if isinstance(r, list):
        return f"{path} is a folder; use list_files."
    data = base64.b64decode(r.get("content", "")) if r.get("content") else b""
    try:
        return _clip(data.decode("utf-8"))
    except UnicodeDecodeError:
        return f"{path} is a binary file ({r.get('size', 0):,} bytes)."


@mcp.tool()
def write_file(repo: str, path: str, content: str, message: str, branch: str = "", confirm: bool = False) -> dict:
    """Create or replace a file in one commit. Replacing an existing file needs confirm=true."""
    full = _full(repo)
    q = f"?ref={urllib.parse.quote(branch)}" if branch else ""
    sha = None
    try:
        sha = _gh("GET", f"/repos/{full}/contents/{urllib.parse.quote(path)}{q}").get("sha")
    except RuntimeError:
        pass
    if sha and not confirm:
        return {"needs_confirmation": f"{path} already exists in {full} and would be replaced. Ask Abrar, then call again with confirm=true."}
    body: dict[str, Any] = {"message": message, "content": base64.b64encode(content.encode()).decode()}
    if sha:
        body["sha"] = sha
    if branch:
        body["branch"] = branch
    r = _gh("PUT", f"/repos/{full}/contents/{urllib.parse.quote(path)}", body)
    return {"committed": r["commit"]["sha"][:12], "url": r["content"]["html_url"]}


@mcp.tool()
def commits(repo: str, branch: str = "", limit: int = 20) -> list[dict]:
    """Recent commits."""
    q = f"&sha={urllib.parse.quote(branch)}" if branch else ""
    r = _gh("GET", f"/repos/{_full(repo)}/commits?per_page={max(1, min(limit, 100))}{q}")
    return [{"sha": c["sha"][:12], "message": c["commit"]["message"].splitlines()[0], "author": c["commit"]["author"]["name"],
             "date": c["commit"]["author"]["date"]} for c in r]


@mcp.tool()
def issues(repo: str, state: str = "open", pulls: bool = False, limit: int = 30) -> list[dict]:
    """Issues (or pull requests with pulls=true) of a repo. state = open | closed | all."""
    full = _full(repo)
    if pulls:
        r = _gh("GET", f"/repos/{full}/pulls?state={state}&per_page={max(1, min(limit, 100))}")
    else:
        r = [i for i in _gh("GET", f"/repos/{full}/issues?state={state}&per_page={max(1, min(limit, 100))}") if "pull_request" not in i]
    return [{"number": i["number"], "title": i["title"], "state": i["state"], "user": i["user"]["login"], "url": i["html_url"]} for i in r]


@mcp.tool()
def read_issue(repo: str, number: int, pull: bool = False) -> dict:
    """An issue or pull request with its comments (and the diff for a pull request)."""
    full = _full(repo)
    i = _gh("GET", f"/repos/{full}/issues/{number}")
    cs = _gh("GET", f"/repos/{full}/issues/{number}/comments?per_page=50")
    out = {"title": i["title"], "state": i["state"], "body": _clip(i.get("body") or ""),
           "comments": [{"user": c["user"]["login"], "body": c["body"][:2000]} for c in cs]}
    if pull or "pull_request" in i:
        out["diff"] = _clip(_gh("GET", f"/repos/{full}/pulls/{number}", accept="application/vnd.github.diff"))
    return out


@mcp.tool()
def create_issue(repo: str, title: str, body: str = "") -> dict:
    """Open an issue."""
    r = _gh("POST", f"/repos/{_full(repo)}/issues", {"title": title, "body": body})
    return {"number": r["number"], "url": r["html_url"]}


@mcp.tool()
def comment(repo: str, number: int, body: str) -> dict:
    """Comment on an issue or pull request."""
    r = _gh("POST", f"/repos/{_full(repo)}/issues/{number}/comments", {"body": body})
    return {"url": r["html_url"]}


@mcp.tool()
def create_pull_request(repo: str, title: str, head: str, base: str = "", body: str = "", draft: bool = False) -> dict:
    """Open a pull request from branch `head` into `base` (default branch if empty)."""
    full = _full(repo)
    base = base or _gh("GET", f"/repos/{full}")["default_branch"]
    r = _gh("POST", f"/repos/{full}/pulls", {"title": title, "head": head, "base": base, "body": body, "draft": draft})
    return {"number": r["number"], "url": r["html_url"]}


@mcp.tool()
def close_or_merge(repo: str, number: int, action: str = "close", confirm: bool = False) -> dict:
    """Close an issue/PR, or merge a PR (action = close | merge). Needs confirm=true."""
    full = _full(repo)
    if not confirm:
        return {"needs_confirmation": f"{action} #{number} in {full} — ask Abrar, then call again with confirm=true."}
    if action == "merge":
        r = _gh("PUT", f"/repos/{full}/pulls/{number}/merge", {"merge_method": "squash"})
        return {"merged": r.get("merged"), "message": r.get("message")}
    _gh("PATCH", f"/repos/{full}/issues/{number}", {"state": "closed"})
    return {"closed": number}


@mcp.tool()
def create_repo(name: str, description: str = "", private: bool = True, confirm: bool = False) -> dict:
    """Create a repository (private by default; a public one needs confirm=true)."""
    if not private and not confirm:
        return {"needs_confirmation": "A public repo is visible to everyone. Ask Abrar, then call again with confirm=true."}
    r = _gh("POST", "/user/repos", {"name": name, "description": description, "private": private, "auto_init": True})
    return {"created": r["full_name"], "private": r["private"], "url": r["html_url"]}


@mcp.tool()
def delete_repo(repo: str, confirm: bool = False) -> dict:
    """Delete a repository — permanent. Needs confirm=true. (Needs the delete_repo scope on the gh login.)"""
    full = _full(repo)
    if not confirm:
        return {"needs_confirmation": f"Deleting {full} is permanent. Ask Abrar, then call again with confirm=true."}
    _gh("DELETE", f"/repos/{full}")
    return {"deleted": full}


@mcp.tool()
def api_get(path: str) -> str:
    """Any read-only GitHub REST call, e.g. "/repos/Arety30/rvtcb/actions/runs?per_page=5"."""
    if not path.startswith("/"):
        path = "/" + path
    return _clip(json.dumps(_gh("GET", path), indent=1))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--http", action="store_true")
    ap.add_argument("--port", type=int, default=8980)
    a = ap.parse_args()
    if not a.http:
        mcp.run()
        return
    mcp.settings.host, mcp.settings.port, mcp.settings.streamable_http_path = "127.0.0.1", a.port, "/mcp"
    try:
        mcp.settings.transport_security.allowed_hosts = ["example.com", "127.0.0.1:*", "localhost:*"]
        mcp.settings.transport_security.allowed_origins = ["https://example.com", "https://chatgpt.com", "https://chat.openai.com"]
    except Exception:  # noqa: BLE001
        pass
    mcp.run(transport="streamable-http")


if __name__ == "__main__":
    main()
