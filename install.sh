#!/usr/bin/env bash
# ---------------------------------------------------------------------------
# prompt-sweeper installer
#
# Installs the package, the Kiro skill, the steering file and the MCP server
# into the active Kiro config directory.
#
# Usage:
#   ./install.sh                      # auto-detect target
#   KIRO_DIR=/path ./install.sh       # force a target
# ---------------------------------------------------------------------------
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer a Python that satisfies requires-python (>=3.10).
if [ -x "/root/.pyenv/versions/3.11.15/bin/python3" ]; then
    export PATH="/root/.pyenv/versions/3.11.15/bin:$PATH"
fi

if [ -n "${KIRO_DIR:-}" ]; then
    TARGET="$KIRO_DIR"
elif [ -d "/projects/.kiro" ]; then
    TARGET="/projects/.kiro"
elif [ -d "$HOME/.kiro" ]; then
    TARGET="$HOME/.kiro"
else
    TARGET="/projects/.kiro"
fi

echo "Installing prompt-sweeper into: $TARGET"
echo ""

mkdir -p "$TARGET/skills" "$TARGET/steering" "$TARGET/mcp-servers"

# 1. Python package (editable, so edits to the repo take effect immediately)
if pip install -e "$REPO_DIR" --quiet 2>/dev/null; then
    echo "  ok  package installed (prompt-sweep on PATH)"
else
    echo "  !!  pip install failed — the skill still works, the CLI will not"
fi

# 2. Skill, including its references
rm -rf "$TARGET/skills/prompt-sweeper"
cp -r "$REPO_DIR/.kiro/skills/prompt-sweeper" "$TARGET/skills/"
echo "  ok  skill      $TARGET/skills/prompt-sweeper/SKILL.md"

# 3. Steering
cp "$REPO_DIR/.kiro/steering/prompt-sweeper.md" "$TARGET/steering/"
echo "  ok  steering   $TARGET/steering/prompt-sweeper.md"

# 4. MCP server
rm -rf "$TARGET/mcp-servers/prompt-sweeper"
cp -r "$REPO_DIR/.kiro/mcp-servers/prompt-sweeper" "$TARGET/mcp-servers/"
chmod +x "$TARGET/mcp-servers/prompt-sweeper/server.py"
echo "  ok  mcp        $TARGET/mcp-servers/prompt-sweeper/server.json"

# 5. Verify
echo ""
echo "Verifying:"
if python3 -c "import promptsweeper" 2>/dev/null; then
    echo "  ok  promptsweeper importable"
else
    echo "  !!  promptsweeper NOT importable"
fi
if python3 -c "from opus5lean import count_tokens" 2>/dev/null; then
    echo "  ok  opus5lean found — exact token counts and maintained pricing available"
else
    echo "  --  opus5lean absent — using fallback price table (install Claude-Opus5)"
fi
if command -v prompt-sweep >/dev/null 2>&1; then
    echo "  ok  prompt-sweep CLI on PATH"
else
    echo "  --  prompt-sweep CLI not on PATH (use: python -m promptsweeper)"
fi

echo ""
echo "Try it — this costs nothing:"
echo "  prompt-sweep generate \"Write a function that retries with backoff\""
echo "  prompt-sweep strategies"
