---
status: completed
spec: [001-content-fetch-endpoint]
summary: 'Updated README.md with two-step flow example, snippet mode example, remote deployment section, and updated REST/MCP tables; added ## Unreleased entry to CHANGELOG.md'
container: semantic-search-exec-016-docs-and-changelog
dark-factory-version: v0.171.1-3-gd94f1fa
created: "2026-05-24T21:00:00Z"
queued: "2026-05-24T21:07:05Z"
started: "2026-05-24T21:16:23Z"
completed: "2026-05-24T21:18:00Z"
branch: dark-factory/content-fetch-endpoint
---

## Summary

- Update README.md with a concrete two-step search-and-consume example using `search_related` then `get_content`
- Add a "Remote Deployment" section noting that callers no longer need filesystem access
- Add an entry for the new `/content` REST endpoint in the REST endpoints table
- Add an entry for the new `get_content` MCP tool in the MCP tools table
- Add `## Unreleased` entry to CHANGELOG.md describing the new endpoint with the `feat:` prefix

## Objective

Update documentation to reflect the new content-fetch capability. README gets the new flow and remote deployment note; CHANGELOG gets the unreleased entry.

## Context

Read these files before making changes:

- `/workspace/README.md` — existing structure, format of examples, how REST endpoints and MCP tools are documented
- `/workspace/CHANGELOG.md` — existing entry format (bullet prefixes, entry structure)
- `/home/node/.claude/plugins/marketplaces/coding/docs/changelog-guide.md` — changelog entry style rules (the `## Unreleased` header format, bullet prefixes)

## Requirements

### README Updates

1. **Add a "Two-Step Flow" example section** in the README, near the existing `search_related` documentation. The example should show:
   ```
   # Step 1: Search for related notes
   search_related("kubernetes deployment")
   # Returns: [{"path": "notes/k8s-guide.md", "score": 0.92}, ...]

   # Step 2: Fetch the content of a result
   get_content(path="notes/k8s-guide.md")
   # Returns: {"path": "/full/resolved/path.md", "content": "# Kubernetes Guide\n...", "mode": "full"}
   ```

2. **Add a "Snippet Mode" example** showing query-focused retrieval:
   ```
   # Get a focused snippet around "service mesh" in the file
   get_content(path="notes/k8s-guide.md", snippet=true, query="service mesh", context_lines=10)
   # Returns: {"path": "...", "content": "...\n## Service Mesh\n...", "mode": "snippet"}
   ```

3. **Add a "Remote Deployment" section** near the "HTTP" server section or as a new subsection. Key points:
   - Callers no longer need filesystem access to the vault directory
   - `get_content` and `GET /content` work over HTTP/MCP from any network location
   - The server enforces path validation — files outside indexed roots are never served

4. **Update the REST endpoints table** to include `/content?path=...&snippet=...&query=...&context_lines=...` alongside existing endpoints.

5. **Update the MCP tools table** to include `get_content(path, snippet, query, context_lines)` alongside existing tools.

### CHANGELOG Updates

6. Add `## Unreleased` section at the top of `CHANGELOG.md` (before `## v0.11.1`). Format:
   ```markdown
   ## Unreleased

   - feat: Add `get_content` MCP tool and `GET /content` REST endpoint for retrieving file content from indexed vaults. Supports full-file and query-focused snippet modes. Enables remote deployment of semantic-search clients without filesystem access to the vault directory.
   ```

## Constraints

- Do NOT change the `search_related` response shape documentation
- Follow existing README and CHANGELOG format conventions exactly
- The `## Unreleased` entry must use `feat:` prefix (for minor version bump)
- Add entry to existing `## Unreleased` section if one already exists (do not replace)
- **CHANGELOG header is frozen**: insert `## Unreleased` AFTER the SemVer preamble (the bullet list explaining MAJOR/MINOR/PATCH ending at line 9 today) and BEFORE the first `## v0.X.Y` entry. Never insert above `# Changelog` and never inside the preamble.

## Verification

```bash
# Verify README changes
grep -n 'get_content' /workspace/README.md
grep -n -i 'remote deployment' /workspace/README.md

# Verify CHANGELOG
grep -n 'get_content\|/content' /workspace/CHANGELOG.md
grep -n '## Unreleased' /workspace/CHANGELOG.md
# Verify Unreleased is the first heading after the preamble (placement check)
awk '/^## /{print NR": "$0}' /workspace/CHANGELOG.md | head -3
# Expected: first line is "## Unreleased", second is "## v0.11.1"

# Final validation
cd /workspace && make precommit
```