# Run semantic-search-http as a Linux systemd user service

Use this setup when you want `semantic-search-http` running continuously so every Claude Code session (and any REST client) shares one warm process instead of spawning its own.

## Why use a user service?

`semantic-search-http` is intended to run continuously. Each stdio MCP client otherwise loads its own copy of torch + sentence-transformers (~400 MB–1 GB RSS). A systemd user unit gives you:

- automatic startup after login
- automatic restart on failure
- one warm indexer shared by N Claude sessions + REST clients
- logs via `journalctl`

## Prerequisites

Install the tool first. Use CPU-only if you don't have a dedicated GPU (saves ~5GB, identical performance for typical vault sizes):

```bash
uv tool install --index https://download.pytorch.org/whl/cpu \
  git+https://github.com/bborbe/semantic-search
```

With CUDA (only if you have a dedicated GPU):

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
Environment=CONTENT_PATH=%h/Documents/Obsidian/Personal,%h/Documents/Obsidian/Trading
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
{"status": "ok", "paths": ["/home/.../Obsidian/Personal", "..."], "indexed_files": 1234}
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
