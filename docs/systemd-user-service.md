# Run semantic-search-http as a Linux systemd user service

Use this setup when you want `semantic-search-http` running continuously so every Claude Code session (and any REST client) shares one warm process instead of spawning its own.

## Why use a user service?

`semantic-search-http` is intended to run continuously. Each stdio MCP client otherwise loads its own copy of torch + sentence-transformers (~400 MB–1 GB RSS). A systemd user unit gives you:

- automatic startup after login
- automatic restart on failure
- one warm indexer shared by N Claude sessions + REST clients
- logs via `journalctl`

## Prerequisites

Install the tool first. Pick **one** of the following:

**CPU-only** — use this unless you have a dedicated NVIDIA GPU. Saves ~5GB of CUDA binaries.

```bash
uv tool install --index https://download.pytorch.org/whl/cpu \
  git+https://github.com/bborbe/semantic-search
```

**CUDA** — for Linux with a dedicated NVIDIA GPU.

```bash
uv tool install git+https://github.com/bborbe/semantic-search
```

Or upgrade an existing install:

```bash
uv tool upgrade semantic-search
```

Verify the binary exists and note the path:

```bash
command -v semantic-search-http
```

Typical location:

- `~/.local/bin/semantic-search-http` (uv tool install)

## 1. Create the user unit

Create `~/.config/systemd/user/semantic-search-http.service`:

```ini
[Unit]
Description=Semantic Search HTTP server (MCP + REST)
After=default.target

[Service]
Type=simple
Environment=CONTENT_PATH=%h/Documents/Obsidian/Personal,%h/Documents/Obsidian/Work
Environment=LOG_LEVEL=INFO
ExecStart=%h/.local/bin/semantic-search-http --host 127.0.0.1 --port 8321
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

**Notes:**

- `%h` expands to the user's home directory — no need for absolute paths.
- `CONTENT_PATH` is comma-separated (no spaces after commas).
- Adjust the paths after `CONTENT_PATH=` for your vaults.
- If `semantic-search-http` is installed elsewhere, update the `ExecStart` path to match `command -v semantic-search-http`.

## 2. Enable and start

Reload systemd so it sees the new unit:

```bash
systemctl --user daemon-reload
```

Enable (start now + on every login):

```bash
systemctl --user enable --now semantic-search-http.service
```

If you log out frequently and want the service to keep running, enable user lingering:

```bash
sudo loginctl enable-linger "$USER"
```

## 3. Manage the service

Stop:

```bash
systemctl --user stop semantic-search-http.service
```

Restart:

```bash
systemctl --user restart semantic-search-http.service
```

Disable (prevent start on login):

```bash
systemctl --user disable semantic-search-http.service
```

Status:

```bash
systemctl --user status semantic-search-http.service
```

## 4. Verify the service is running

Check unit state:

```bash
systemctl --user is-active semantic-search-http.service
```

Check the process:

```bash
ps -ef | grep semantic-search-http | grep -v grep
```

Check the HTTP endpoint:

```bash
curl http://127.0.0.1:8321/health
```

Expected response:

```json
{"status": "ok", "paths": ["/home/YOUR_USER/Documents/Obsidian/Personal", "..."], "indexed_files": 1234}
```

Follow logs:

```bash
journalctl --user -u semantic-search-http.service -f
```

First run downloads the embedding model (~90 MB) and builds the index. Subsequent runs are near-instant.

## 5. Point Claude Code at the service

Edit `~/.claude/mcp-personal.json` (or any mcp config) so Claude Code connects via HTTP instead of spawning its own stdio server:

```json
{
  "mcpServers": {
    "semantic-search": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp"
    }
  }
}
```

All Claude Code sessions now share the one warm indexer.

## 6. Upgrade flow

```bash
uv tool upgrade semantic-search
systemctl --user restart semantic-search-http.service
```

## One instance, many scopes

Run **one** `semantic-search-http` instance and give each client its own view with a
scope, rather than one instance per content domain. A per-domain instance means a
per-domain index: five daemons over overlapping content measured 10.3 GB combined
`phys_footprint` on 2026-09-11, almost all of it duplicated index data. One daemon holds
one union index; the scope filter narrows it per client.

Scopes are declared in a scope map and selected with `?scope=<name>`. The map is named by
the `SEMANTIC_SCOPE_MAP` env var when that variable is set; when it is unset or empty, the
server reads `~/.config/semantic-search/config.yaml`. See
[design/per-vault-scoping.md](design/per-vault-scoping.md) for the contract, the five scope
names, and the fail-closed default.

MCP clients differ only by the scope in their URL:

```json
{
  "mcpServers": {
    "semantic-search-personal": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp?scope=personal"
    },
    "semantic-search-brogrammers": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp?scope=brogrammers"
    }
  }
}
```

Two clients, two scopes, **one port and one index**.

### Adding a scope

Add a name and its ordered root list to the scope map — `scopes.yaml` in the repo when
`SEMANTIC_SCOPE_MAP` points at it, otherwise `~/.config/semantic-search/config.yaml` — then
restart the unit: the scope map is read at startup. A scope name is a dictionary key, never
a filesystem path and never a shell fragment.

### If you genuinely need a second instance

The label-suffix pattern below still works, and each instance still needs its own unit
file, `Port`, and scope map (`SEMANTIC_SCOPE_MAP`, or the default
`~/.config/semantic-search/config.yaml`). **Each instance MUST bind a unique port** — TCP
ports cannot be shared, so if two units try to bind 8321 only one wins and the other
restarts in a loop. But a second instance means a second full index, which is exactly
the cost the scope design exists to avoid: prefer a scope.

| Component | Default | With suffix `personal` |
|-----------|---------|------------------------|
| Unit file | `semantic-search-http.service` | `semantic-search-http-personal.service` |
| Port | `8321` | e.g. `8322` |
| MCP server name | `semantic-search` | `semantic-search-personal` |

Enable each unit independently:

```bash
systemctl --user enable --now semantic-search-http-personal.service
systemctl --user enable --now semantic-search-http-work.service
```

Verify:

```bash
systemctl --user list-units 'semantic-search-http*'
```

`/semantic-search:configure` handles the label suffix when you choose "Add another instance" in its pre-flight prompt.

## Troubleshooting

### Unit fails to start (`systemctl --user status` shows `failed`)

Check logs:

```bash
journalctl --user -u semantic-search-http.service -n 50
```

Common causes:

- wrong `ExecStart` path
- `CONTENT_PATH` directories do not exist
- port 8321 already in use (`ss -ltnp | grep 8321`)
- missing Python dependencies (reinstall with `uv tool upgrade semantic-search`)

### `curl` hangs on first start

First run is downloading the model or building the index. Watch the log:

```bash
journalctl --user -u semantic-search-http.service -f
```

Typical first-run time: 5–30 seconds depending on vault size.

### Port 8321 already in use

Identify the owner:

```bash
ss -ltnp | grep 8321
```

Change the port in the unit file (`--port 8322`), reload, restart, and update the Claude Code `url` to match:

```bash
systemctl --user daemon-reload
systemctl --user restart semantic-search-http.service
```

### Service dies when I log out

Enable lingering so user units keep running:

```bash
sudo loginctl enable-linger "$USER"
```

### Changed `CONTENT_PATH` but service still indexes old paths

Environment changes require `daemon-reload` + restart:

```bash
systemctl --user daemon-reload
systemctl --user restart semantic-search-http.service
```

## Related

- `README.md` — overview and binaries
- `docs/launchd-service.md` — macOS equivalent
- `semantic-search-http --help` — CLI flags
