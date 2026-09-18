#!/bin/bash
# Register this checkout's stdio server in the Claude desktop app's config.
# Run with the app quit: a running app rewrites the file from memory and
# drops edits made underneath it.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
REPO="${BOOKEO_REPO:-$REPO}"
CONFIG="${CLAUDE_DESKTOP_CONFIG:-$HOME/Library/Application Support/Claude/claude_desktop_config.json}"

# Captured first: under pipefail, grep -q exiting early would SIGPIPE ps and
# make the whole test read as false.
PROCS="$(ps -axo comm)"
if [ -z "${CLAUDE_DESKTOP_CONFIG:-}" ] && grep -q "/Claude.app/Contents/MacOS/Claude$" <<<"$PROCS"; then
  echo "Claude is running; quit it first (Cmd+Q), then re-run." >&2
  exit 1
fi

python3 - "$CONFIG" "$REPO" <<'PY'
import json, sys
path, repo = sys.argv[1], sys.argv[2]
config = json.load(open(path))
config.setdefault("mcpServers", {})["bookeo"] = {
    "command": "/bin/sh",
    "args": ["-c", f"cd '{repo}' && exec .venv/bin/python -m bookeo_mcp.server"],
}
with open(path, "w") as f:
    json.dump(config, f, indent=2)
    f.write("\n")
print("mcpServers:", ", ".join(config["mcpServers"]))
PY
