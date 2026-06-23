#!/usr/bin/env bash
# Check for breaking API changes between the current spec3.yaml and a base branch.
# Exits 1 if breaking changes are detected.
#
# Dependencies (install one of these):
#   npm install -g @openapitools/openapi-diff        (oasdiff alternative)
#   pip install openapi-diff-tool
#   brew install tufin/tufin/oasdiff                 (recommended — most complete)
#
# Usage:
#   bash scripts/diff.sh main            # compare current branch vs main
#   bash scripts/diff.sh development     # compare vs development
#   bash scripts/diff.sh HEAD~1          # compare vs previous commit

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE_REF="${1:-main}"
SPEC="$ROOT/spec3.yaml"
TMP_DIR=$(mktemp -d)
BASE_SPEC="$TMP_DIR/spec3-base.yaml"

cleanup() { rm -rf "$TMP_DIR"; }
trap cleanup EXIT

echo "Comparing spec3.yaml: current vs $BASE_REF"
echo "──────────────────────────────────────────"

# Extract the base spec from git
if ! git show "$BASE_REF:spec3.yaml" > "$BASE_SPEC" 2>/dev/null; then
  echo "Could not get spec3.yaml from ref '$BASE_REF'"
  echo "Make sure the branch/ref exists and spec3.yaml is tracked."
  exit 1
fi

BREAKING=0

# ── oasdiff (preferred) ────────────────────────────────────────────────────
if command -v oasdiff &>/dev/null; then
  echo "Using oasdiff..."

  # Breaking changes check
  if oasdiff breaking "$BASE_SPEC" "$SPEC" --fail-on ERR; then
    echo "✓ No breaking changes detected"
  else
    echo "✗ Breaking changes detected — review above before merging"
    BREAKING=1
  fi

  echo ""
  echo "── Full changelog ─────────────────────────────"
  oasdiff changelog "$BASE_SPEC" "$SPEC" || true

# ── openapi-diff (fallback) ────────────────────────────────────────────────
elif command -v openapi-diff &>/dev/null; then
  echo "Using openapi-diff..."
  if openapi-diff "$BASE_SPEC" "$SPEC" --fail-on-incompatible; then
    echo "✓ No breaking changes detected"
  else
    echo "✗ Breaking changes detected"
    BREAKING=1
  fi

else
  echo "No diff tool found. Install one of:"
  echo "  brew install tufin/tufin/oasdiff"
  echo "  npm install -g @openapitools/openapi-diff"
  echo ""
  echo "Falling back to git diff (no semantic analysis):"
  git diff "$BASE_REF" -- spec3.yaml || true
  echo ""
  echo "⚠ Manual review required — could not detect breaking changes automatically."
  exit 0
fi

echo "──────────────────────────────────────────"
exit $BREAKING
