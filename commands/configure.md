---
allowed-tools: Read, Bash, Write, Edit, AskUserQuestion
description: Configure semantic-search HTTP service (launchd on macOS, systemd-user on Linux) and register MCP in Claude Code
---

## Purpose

Walk the user through installing `semantic-search-http` as a background service so every Claude Code session shares one warm indexer. Covers:

1. Tool install via `uv tool install`
2. Service unit creation (launchd on macOS, systemd-user on Linux)
3. MCP registration in Claude config

## Process

### Step 1: Detect platform

```bash
uname -s
```

- `Darwin` → macOS, use launchd flow (Step 3a)
- `Linux` → Linux, use systemd-user flow (Step 3b)
- Other → STOP, report unsupported platform

### Step 2: Verify or install the binary

Check if the binary exists:

```bash
command -v semantic-search-http || echo "MISSING"
```

If missing, ask the user (single yes/no):

> `semantic-search-http` not found. Install now via `uv tool install` (CPU-only)?

If yes, run:

```bash
uv tool install --index https://download.pytorch.org/whl/cpu \
  git+https://github.com/bborbe/semantic-search
```

Re-check `command -v semantic-search-http`. If still missing, STOP and surface the install error.

Capture the absolute binary path for Step 3.

### Step 3: Ask for CONTENT_PATH

Use AskUserQuestion to collect the content directories. Default suggestions if user has known vaults (check `~/Documents/Obsidian/`):

> Which directories should be indexed? (comma-separated, absolute paths)

Validate each directory exists. If any are missing, list them and ask the user to confirm or correct.

### Step 3a: macOS (launchd)

Reference: `docs/launchd-service.md` in the plugin repo.

1. Confirm port (default `8321`); check `lsof -i :8321` — if occupied, ask for an alternative.
2. Create `~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist` from the template in `docs/launchd-service.md`, substituting:
   - Binary path (from Step 2)
   - `CONTENT_PATH` (from Step 3, comma-separated, absolute paths — launchd does not expand `~`)
   - Port
3. Show the rendered plist to the user and ask for confirmation before writing.
4. Load: `launchctl load ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist`
5. Verify: `launchctl list | grep semantic-search-http` — status column should be `0` or `-`.
6. Health check (allow up to 30s for first-run model download):
   ```bash
   curl -s http://127.0.0.1:8321/health
   ```

### Step 3b: Linux (systemd-user)

Reference: `docs/systemd-user-service.md` in the plugin repo.

1. Confirm port (default `8321`); check `ss -ltnp | grep 8321` — if occupied, ask for an alternative.
2. Create `~/.config/systemd/user/semantic-search-http.service` from the template in `docs/systemd-user-service.md`, substituting:
   - Binary path (use `%h/.local/bin/semantic-search-http` if installed via `uv tool install`)
   - `CONTENT_PATH` (use `%h` for home dir where appropriate)
   - Port
3. Show the rendered unit file and ask for confirmation before writing.
4. Reload + enable:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now semantic-search-http.service
   ```
5. Optionally enable lingering (ask user):
   ```bash
   sudo loginctl enable-linger "$USER"
   ```
6. Health check:
   ```bash
   curl -s http://127.0.0.1:8321/health
   ```

### Step 4: Register MCP in Claude Code

Locate the user's MCP config file. Common locations (in priority order):

- `~/.claude/mcp-personal.json` (if it already exists)
- `~/.claude.json` (Claude Code default)

Ask the user which config to update if multiple exist.

Read the existing file. Add or update the `mcpServers.semantic-search` entry:

```json
{
  "mcpServers": {
    "semantic-search": {
      "type": "http",
      "url": "http://127.0.0.1:<PORT>/mcp"
    }
  }
}
```

Show the diff before writing.

### Step 5: Final verification

Report:

```
✅ semantic-search-http running on port <PORT>
✅ Indexing: <CONTENT_PATH>
✅ Health: <indexed_files> files indexed
✅ MCP registered in <config_path>

Restart Claude Code to pick up the new MCP server.
```

## Failure Modes

- **Binary install failed** → surface `uv` error, suggest `uv self update` or manual `pip install`
- **Port in use** → list owner from `lsof`/`ss`, ask for alternative port
- **Health check times out** → check service logs (`/tmp/semantic-search-http.log` on macOS, `journalctl --user -u semantic-search-http.service` on Linux)
- **MCP registration fails** → show the JSON snippet for manual paste

## Reference Docs

- `docs/launchd-service.md` — full macOS guide
- `docs/systemd-user-service.md` — full Linux guide
- `README.md` — overview of `semantic-search-http` modes
