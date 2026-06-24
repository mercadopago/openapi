#!/usr/bin/env bash
# Validate spec3.yaml against OpenAPI 3.1 rules.
# Exits 1 on any violation.
#
# Dependencies (install once):
#   pip install openapi-spec-validator
#   npm install -g @stoplight/spectral-cli
#
# Usage:
#   bash scripts/validate.sh               # validate spec3.yaml
#   bash scripts/validate.sh by-site/MLB   # validate a by-site spec

set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TARGET="${1:-}"

# If an argument is passed and it's a directory, validate all spec3.yaml in it
if [[ -n "$TARGET" && -d "$ROOT/$TARGET" ]]; then
  SPECS=("$ROOT/$TARGET/spec3.yaml")
elif [[ -n "$TARGET" && -f "$ROOT/$TARGET" ]]; then
  SPECS=("$ROOT/$TARGET")
else
  SPECS=("$ROOT/spec3.yaml")
fi

ERRORS=0

for SPEC in "${SPECS[@]}"; do
  echo "──────────────────────────────────────────"
  echo "Validating: $SPEC"
  echo "──────────────────────────────────────────"

  # ── 1. openapi-spec-validator (structural OpenAPI 3.1 compliance) ──────────
  if command -v openapi-spec-validator &>/dev/null; then
    echo "[1/2] openapi-spec-validator..."
    if openapi-spec-validator "$SPEC"; then
      echo "  ✓ openapi-spec-validator passed"
    else
      echo "  ✗ openapi-spec-validator FAILED"
      ERRORS=$((ERRORS + 1))
    fi
  else
    echo "[1/2] openapi-spec-validator not found — skipping"
    echo "      Install: pip install openapi-spec-validator"
  fi

  # ── 2. Spectral (Stoplight rules — style + completeness) ──────────────────
  if command -v spectral &>/dev/null; then
    echo "[2/2] spectral lint..."
    SPECTRAL_RULESET="$ROOT/.spectral.yaml"
    if [[ -f "$SPECTRAL_RULESET" ]]; then
      RULESET_ARG="--ruleset $SPECTRAL_RULESET"
    else
      RULESET_ARG="--ruleset spectral:oas"
    fi
    if spectral lint "$SPEC" $RULESET_ARG --fail-severity warn; then
      echo "  ✓ spectral passed"
    else
      echo "  ✗ spectral FAILED"
      ERRORS=$((ERRORS + 1))
    fi
  else
    echo "[2/2] spectral not found — skipping"
    echo "      Install: npm install -g @stoplight/spectral-cli"
  fi

  # ── 3. Self-containment check: zero external \$ref ─────────────────────────
  echo "[3/3] self-containment check (no external \$ref)..."
  EXTERNAL_REFS=$(grep -c '\$ref: "schemas/' "$SPEC" 2>/dev/null || true)
  if [[ "$EXTERNAL_REFS" -eq 0 ]]; then
    echo "  ✓ no external \$ref"
  else
    echo "  ✗ found $EXTERNAL_REFS external \$ref(s) — run 'python scripts/bundle.py --schemas-only'"
    ERRORS=$((ERRORS + 1))
  fi

done

echo "──────────────────────────────────────────"
if [[ "$ERRORS" -eq 0 ]]; then
  echo "All validations passed."
  exit 0
else
  echo "$ERRORS validation error(s) found."
  exit 1
fi
