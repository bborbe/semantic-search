---
allowed-tools: Read, Bash, Write, Edit, AskUserQuestion
description: Configure semantic-search HTTP service (launchd on macOS, systemd-user on Linux) and register MCP in Claude Code
---

## Purpose

Walk the user through installing `semantic-search-http` as a background service so every Claude Code session shares one warm indexer. Covers:

1. Pre-flight — detect existing instances, skip or add more
2. Tool install via `uv tool install`
3. Service unit creation (launchd on macOS, systemd-user on Linux), with optional instance label for multi-instance setups
4. MCP registration in Claude config

## Process

### Step 0: Pre-flight detection

Probe for an existing service:

```bash
curl -fsS --max-time 3 http://127.0.0.1:8321/health 2>/dev/null
```

If it returns `{"status":"ok",...}`, list existing service units:

- macOS: `launchctl list | grep semantic-search-http`
- Linux: `systemctl --user list-units 'semantic-search-http*' --no-legend`

Also check current MCP registration:

```bash
jq '.mcpServers | to_entries[] | select(.key | test("semantic"))' ~/.claude/mcp-personal.json ~/.claude.json 2>/dev/null
```

Report findings, then ask (single AskUserQuestion):

> A semantic-search-http service is already running on port 8321 (instances: <list>). What would you like to do?
> 1. Skip — already configured
> 2. Add another instance (different port + label)
> 3. Reconfigure existing instance

- **1 (skip)** → STOP with summary
- **2 (add instance)** → continue with Step 2; in Step 3 ask for instance label + alternative port
- **3 (reconfigure)** → continue with Step 2, reuse the existing label, propose unloading old plist before writing new

If no service is running, continue normally with Step 2.

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

### Step 3: Collect configuration

**Compute default port:**

- **Greenfield (no existing instances)** → default `8321`.
- **"Add another instance" path** → scan ports of all existing semantic-search-http instances (from Step 0) and propose `max(existing) + 1`. Each instance MUST have a unique port (TCP can't be shared).
- **"Reconfigure" path** → reuse the existing instance's current port unless the user wants to change it.

Then probe the chosen default with `lsof -i :<port>` (macOS) / `ss -ltnp | grep <port>` (Linux). If occupied by a non-semantic-search process, increment until a free port is found.

Use AskUserQuestion to collect (in one batch):

1. **Instance label** (default empty) — used as plist/unit suffix. Empty → `com.github.bborbe.semantic-search-http`. Set to e.g. `personal` → `com.github.bborbe.semantic-search-http-personal`. Required when adding a second instance.
2. **Port** (default computed above) — confirm or change.
3. **CONTENT_PATH** — comma-separated absolute paths. Default suggestions if `~/Documents/Obsidian/` exists.

Validate each directory exists. List missing ones and ask for correction.

### Step 3a: macOS (launchd)

Reference: `docs/launchd-service.md` (see "Multi-instance" section for label pattern).

1. Build plist label: `com.github.bborbe.semantic-search-http[-<label>]`
2. Plist path: `~/Library/LaunchAgents/<label>.plist`
3. Probe port: `lsof -i :<port>` — if occupied by a non-semantic-search process, ask for an alternative.
4. Render plist from the template in `docs/launchd-service.md`, substituting:
   - `Label` → full label from step 1
   - Binary path (from Step 2)
   - `CONTENT_PATH` (from Step 3, comma-separated, absolute paths — launchd does not expand `~`)
   - Port
   - `StandardOutPath` / `StandardErrorPath` → `/tmp/<label>.log`
5. Show the rendered plist to the user and ask for confirmation before writing.
6. If reconfigure path: `launchctl unload <existing-plist>` first.
7. Load: `launchctl load ~/Library/LaunchAgents/<label>.plist`
8. Verify: `launchctl list | grep <label>` — status column should be `0` or `-`.
9. Health check (allow up to 30s for first-run model download):
   ```bash
   curl -s http://127.0.0.1:<port>/health
   ```

### Step 3b: Linux (systemd-user)

Reference: `docs/systemd-user-service.md`.

1. Build unit name: `semantic-search-http[-<label>].service`
2. Unit path: `~/.config/systemd/user/<unit>`
3. Probe port: `ss -ltnp | grep <port>` — if occupied, ask for alternative.
4. Render unit from the template in `docs/systemd-user-service.md`, substituting:
   - `Description` includes label
   - Binary path (use `%h/.local/bin/semantic-search-http` if installed via `uv tool install`)
   - `CONTENT_PATH` (use `%h` for home dir where appropriate)
   - Port
5. Show the rendered unit file and ask for confirmation before writing.
6. If reconfigure path: `systemctl --user stop <old-unit>` first.
7. Reload + enable:
   ```bash
   systemctl --user daemon-reload
   systemctl --user enable --now <unit>
   ```
8. Optionally enable lingering (ask user):
   ```bash
   sudo loginctl enable-linger "$USER"
   ```
9. Health check:
   ```bash
   curl -s http://127.0.0.1:<port>/health
   ```

### Step 4: Register MCP in Claude Code

Locate the user's MCP config file. Common locations (in priority order):

- `~/.claude/mcp-personal.json` (if it already exists)
- `~/.claude.json` (Claude Code default)

Ask the user which config to update if multiple exist.

MCP server name: `semantic-search[-<label>]`. Read the existing file. Add or update:

```json
{
  "mcpServers": {
    "semantic-search-<label>": {
      "type": "http",
      "url": "http://127.0.0.1:<PORT>/mcp"
    }
  }
}
```

(No suffix when label is empty.)

Show the diff before writing.

### Step 5: Final verification

Report:

```
✅ semantic-search-http[-<label>] running on port <PORT>
✅ Indexing: <CONTENT_PATH>
✅ Health: <indexed_files> files indexed
✅ MCP registered as 'semantic-search[-<label>]' in <config_path>

Restart Claude Code to pick up the new MCP server.
```

## Failure Modes

- **Binary install failed** → surface `uv` error, suggest `uv self update` or manual `pip install`
- **Port in use by non-semantic-search process** → list owner from `lsof`/`ss`, ask for alternative port
- **Health check times out** → check service logs (`/tmp/<label>.log` on macOS, `journalctl --user -u <unit>` on Linux)
- **MCP registration fails** → show the JSON snippet for manual paste

## Reference Docs

- `docs/launchd-service.md` — full macOS guide, including multi-instance section
- `docs/systemd-user-service.md` — full Linux guide
- `README.md` — overview of `semantic-search-http` modes
