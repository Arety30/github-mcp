# github-mcp

A single-file Model Context Protocol (MCP) server by Syed Abrar Ali that gives an MCP client (a chat assistant, an IDE agent or any other MCP-capable tool) controlled access to one GitHub account through the GitHub REST API. An assistant that can read repositories, commit files, open issues and review pull requests is useful, but handing it a raw personal access token is risky. This server never puts the token in the conversation (it is fetched on demand from the GitHub CLI login and never returned to the client), and it gates every destructive or public-facing action behind an explicit `confirm=true` flag so the client has to describe the change and get a human "yes" before it happens.

## Where it is used

It runs on the owner's laptop as one of several small personal MCP servers. In HTTP mode it listens only on the loopback interface; a reverse-proxy gateway maps a secret URL path to that local port so a remote chat assistant can connect to it as a connector. In stdio mode it is launched directly by local MCP clients (editor agents, CLI assistants) that need to look at or change the owner's repositories.

## How it works

Everything lives in `server.py`, built on `mcp.server.fastmcp.FastMCP`.

| Piece | Role |
| --- | --- |
| `_token()` | Runs `gh auth token` for every request; the token is never cached or exposed. |
| `_gh(method, path, body, accept)` | Thin `urllib` wrapper around `https://api.github.com` with Bearer auth, the `X-GitHub-Api-Version` header and JSON encoding. Asking for the `diff` media type returns raw text (used for pull-request diffs). HTTP errors become `RuntimeError("GitHub <code>: <body>")` so the client sees a readable message. |
| `_full(repo)` | Expands a bare repository name to `<login>/<name>` using `/user`, so tools accept either form. |
| `_clip(s)` | Truncates any text result to 15,000 characters and appends how much was cut. |
| `mcp = FastMCP("github", instructions=...)` | Server instructions tell the model to read freely, keep new repos private, never write secrets into files or issues, and ask before anything destructive. |
| `main()` | Argument parsing and transport selection (stdio or streamable HTTP). |

Tools exposed:

| Tool | What it does | Needs `confirm=true` |
| --- | --- | --- |
| `whoami` | Login, name and repo counts of the authenticated account | no |
| `list_repos`, `search_repos`, `search_code` | Own repos (most recently pushed first), global repo search, code search (optionally scoped to one repo) | no |
| `list_files`, `read_file` | Browse a path or read a text file at an optional ref; binary files are reported, not returned | no |
| `write_file` | Create or replace a file in one commit (fetches the existing blob SHA first) | only when replacing an existing file |
| `commits`, `issues`, `read_issue` | Recent commits; issues or pull requests by state; one issue/PR with comments and (for PRs) the diff | no |
| `create_issue`, `comment`, `create_pull_request` | Open an issue, comment on an issue/PR, open a PR (base defaults to the default branch) | no |
| `close_or_merge` | Close an issue/PR or squash-merge a PR | yes |
| `create_repo` | Create a repo, private by default and auto-initialised | only for a public repo |
| `delete_repo` | Permanently delete a repo (requires the `delete_repo` scope on the gh login) | yes |
| `api_get` | Any read-only REST path, returned as pretty JSON | no |

Decision rule: a guarded tool called without `confirm=true` does not fail; it returns `{"needs_confirmation": "..."}` describing exactly what would change, so the model can relay that to the user and call again.

There is no database or state; every call goes straight to GitHub.

## Setup

Requirements:

- Python 3.10 or newer
- the `mcp` package (`pip install "mcp[cli]"`)
- the GitHub CLI (`gh`) installed and logged in: `gh auth login`
- for `delete_repo`, add the scope once: `gh auth refresh -h github.com -s delete_repo`

```bash
git clone https://github.com/USER/github-mcp.git
cd github-mcp
python -m venv .venv && . .venv/bin/activate
pip install "mcp[cli]"
```

Configuration is by constants at the top of `server.py`:

- `MAX_TEXT` (15000) — result truncation length.
- `GH` — path to the `gh` binary (auto-detected, falls back to `/usr/bin/gh`).
- In `main()`, `transport_security.allowed_hosts` / `allowed_origins` list the public gateway hostname and client origins that may reach the HTTP transport. Replace them with your own (for example `mcp.example.com`).

No environment variables or `.env` file are needed.

## Usage

Stdio (for local MCP clients):

```bash
python server.py
```

Example client configuration:

```json
{ "mcpServers": { "github": { "command": "python", "args": ["~/github-mcp/server.py"] } } }
```

Streamable HTTP (for remote connectors through a reverse proxy):

```bash
python server.py --http --port 8980
# serves http://127.0.0.1:8980/mcp
```

Typical interactions once connected:

- "Which GitHub account is this?" → `whoami`
- "List my repos and show the README of my-project" → `list_repos`, `read_file(repo="my-project", path="README.md")`
- "Open an issue in my-project titled 'Fix login timeout'" → `create_issue`
- "Merge PR #12" → `close_or_merge(action="merge")` returns `needs_confirmation`; after the user agrees the client calls again with `confirm=true`
- "How many workflow runs failed lately?" → `api_get("/repos/USER/my-project/actions/runs?per_page=5")`

## Project structure

```
github-mcp/
└── server.py     # FastMCP server: GitHub REST helpers, 17 tools, stdio/HTTP entry point
```

## Limitations and notes

- Authentication is only as strong as the `gh` login; the server adds no scopes or per-tool permissions of its own.
- The HTTP transport has no authentication built in. It binds to the loopback interface and relies on the fronting proxy (secret path, TLS) for access control; do not expose the port directly.
- `close_or_merge` always squash-merges; other merge strategies are not offered.
- `read_file` and `api_get` are capped at 15,000 characters; `read_issue` comments are capped at 2,000 characters each and 50 comments.
- Search tools return at most 50 results and are subject to GitHub's search rate limits.
- `write_file` commits one file per call; there is no multi-file commit or branch creation tool (use `api_get` for read-only inspection and the GitHub UI or CLI for anything else).
- Confirmation prompts and server instructions are written for the owner's own use and mention him by first name; adjust the wording if you deploy it for someone else.
