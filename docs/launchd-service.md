# Run semantic-search-http as a macOS launchd service

Use this setup when you want `semantic-search-http` running continuously so every Claude Code session (and any REST client) shares one warm process instead of spawning its own.

## Why use a launchd service?

`semantic-search-http` is intended to run continuously. Each stdio MCP client otherwise loads its own copy of torch + sentence-transformers (~400 MB–1 GB RSS). A launchd user agent gives you:

- automatic startup after login
- automatic restart if the process exits
- one warm indexer shared by N Claude sessions + REST clients
- easier status and log inspection

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

Typical locations:

- `~/.local/bin/semantic-search-http` (uv tool install)
- `/opt/homebrew/bin/semantic-search-http`

## 1. Create a launch agent

Create `~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist`:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.github.bborbe.semantic-search-http</string>
    <key>ProgramArguments</key>
    <array>
        <string>/Users/YOUR_USER/.local/bin/semantic-search-http</string>
        <string>--host</string>
        <string>127.0.0.1</string>
        <string>--port</string>
        <string>8321</string>
    </array>
    <key>EnvironmentVariables</key>
    <dict>
        <key>CONTENT_PATH</key>
        <string>/Users/YOUR_USER/Documents/Obsidian/Personal,/Users/YOUR_USER/Documents/Obsidian/Trading</string>
        <key>LOG_LEVEL</key>
        <string>INFO</string>
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/semantic-search-http.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/semantic-search-http.log</string>
</dict>
</plist>
```

**Important:**

- Replace the binary path with the output of `command -v semantic-search-http`.
- **launchd does NOT expand `~`** — use absolute paths everywhere, including inside `CONTENT_PATH`.
- `CONTENT_PATH` is comma-separated (no spaces after commas).

Load and start the service:

```bash
launchctl load ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
```

## 2. Manage the service

Stop:

```bash
launchctl unload ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
```

Restart (stop + start):

```bash
launchctl unload ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
launchctl load ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
```

## 3. Verify the service is running

Check launchd status:

```bash
launchctl list | grep semantic-search-http
```

A running service shows `0` or `-` in the status column. A non-zero exit code indicates a problem.

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
{"status": "ok", "paths": ["/Users/YOUR_USER/Documents/Obsidian/Personal", "..."], "indexed_files": 1234}
```

Check logs:

```bash
tail -f /tmp/semantic-search-http.log
```

First run downloads the embedding model (~90 MB) and builds the index. Subsequent runs are near-instant.

## 4. Point Claude Code at the service

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

## 5. Upgrade flow

```bash
uv tool upgrade semantic-search
launchctl unload ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
launchctl load ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
```

## Troubleshooting

### Service keeps restarting (non-zero exit in `launchctl list`)

Check `/tmp/semantic-search-http.log`. Common causes:

- wrong binary path in plist
- missing or invalid `CONTENT_PATH` (directories must exist)
- port 8321 already in use (`lsof -i :8321`)

### Plist loaded but `curl` hangs

First run is downloading the model or building the index. Watch the log:

```bash
tail -f /tmp/semantic-search-http.log
```

Typical first-run time: 5–30 seconds depending on vault size.

### Port 8321 already in use

Either an existing REST server is running, or another process grabbed the port. Identify it:

```bash
lsof -i :8321
```

Change the port in the plist (`--port 8322`) and update your Claude Code `url` to match.

### Claude Code says "MCP connection failed"

- Verify the service responds: `curl http://127.0.0.1:8321/mcp -X POST -H 'Content-Type: application/json' -d '{"jsonrpc":"2.0","method":"initialize","id":1,"params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"test","version":"1"}}}'`
- Confirm the `url` in `mcp-personal.json` ends with `/mcp` (not just `/`)
- Restart Claude Code after editing the MCP config

### Changed `CONTENT_PATH` but service still indexes old paths

Restart the service after any `EnvironmentVariables` edit (launchd only reads the plist on load):

```bash
launchctl unload ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
launchctl load ~/Library/LaunchAgents/com.github.bborbe.semantic-search-http.plist
```

## Related

- `README.md` — overview and binaries
- `docs/systemd-user-service.md` — Linux equivalent
- `semantic-search-http --help` — CLI flags
