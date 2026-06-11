#!/usr/bin/env python3
"""
classify_schemas.py

Reads spec3.yaml components/schemas and distributes each schema into
the corresponding schemas/{domain}.yaml fragment file, based on the
app → tag → domain mapping in apps.yaml.

This is the REVERSE of bundle.py --schemas-only:
  bundle.py:         schemas/*.yaml → spec3.yaml (consolidate)
  classify_schemas:  spec3.yaml     → schemas/*.yaml (distribute)

Run this AFTER sync_spechub.py merges new schemas into spec3.yaml,
so the domain fragment files stay in sync with spec3.yaml.

Usage:
    python scripts/classify_schemas.py
    python scripts/classify_schemas.py --dry-run   # show what would change
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Any

import yaml

ROOT          = Path(__file__).resolve().parent.parent
SPEC3_PATH    = ROOT / "spec3.yaml"
SCHEMAS_DIR   = ROOT / "schemas"
APPS_CONFIG   = ROOT / "apps.yaml"

# ── Domain mapping: tag → schema file ─────────────────────────────────────
# Derived from apps.yaml tags + common patterns.
# Add new domains here when new API groups are added to openapi.
TAG_TO_SCHEMA_FILE: dict[str, str] = {
    "Payments":            "payments",
    "Orders":              "orders",
    "Checkout":            "checkout",
    "Preferences":         "checkout",
    "Customers":           "customers",
    "Cards":               "customers",
    "Subscriptions":       "subscriptions",
    "Plans":               "subscriptions",
    "Invoices":            "subscriptions",
    "Chargebacks":         "common",
    "Merchant Orders":     "webhooks",
    "Merchant Order":      "webhooks",
    "OAuth":               "oauth",
    "Wallet Connect":      "oauth",
    "Payment Methods":     "common",
    "Identification Types":"common",
    "Cancellations":       "common",
    "Stores":              "common",
    "POS":                 "common",
    "Reports":             "reports",
    "Claims":              "claims",
}

# Schema name prefix patterns → domain file (fallback when tag can't determine domain)
NAME_PATTERNS: list[tuple[str, str]] = [
    (r"^Payment",       "payments"),
    (r"^Refund",        "payments"),
    (r"^Order",         "orders"),
    (r"^Preference",    "checkout"),
    (r"^Customer",      "customers"),
    (r"^Card",          "customers"),
    (r"^Subscription",  "subscriptions"),
    (r"^Authorized",    "subscriptions"),
    (r"^Plan",          "subscriptions"),
    (r"^Invoice",       "subscriptions"),
    (r"^Oauth",         "oauth"),
    (r"^Token",         "oauth"),
    (r"^Report",        "reports"),
    (r"^Claim",         "claims"),
    (r"^Webhook",       "webhooks"),
    (r"^Merchant",      "webhooks"),
    (r"^Notification",  "webhooks"),
]

FALLBACK_DOMAIN = "common"


def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=True,
                  default_flow_style=False, width=120)


def build_schema_usage_index(spec: dict[str, Any]) -> dict[str, set[str]]:
    """
    Build a map of schema_name → {tags used in operations that reference it}.
    Walks all paths/operations looking for $ref to #/components/schemas/{name}.
    """
    index: dict[str, set[str]] = {}

    def collect_refs(obj: Any, current_tags: list[str]) -> None:
        if isinstance(obj, dict):
            if "$ref" in obj:
                ref = obj["$ref"]
                if ref.startswith("#/components/schemas/"):
                    name = ref.split("/")[-1]
                    index.setdefault(name, set()).update(current_tags)
            else:
                for v in obj.values():
                    collect_refs(v, current_tags)
        elif isinstance(obj, list):
            for item in obj:
                collect_refs(item, current_tags)

    for path, methods in spec.get("paths", {}).items():
        for method, operation in methods.items():
            if not isinstance(operation, dict):
                continue
            tags = operation.get("tags", [])
            collect_refs(operation, tags)

    return index


def classify_schema(name: str, tags: set[str]) -> str:
    """
    Determine which schemas/*.yaml file a schema belongs to.
    Priority: tag mapping → name pattern → fallback (common).
    """
    # 1. Try tag-based mapping
    for tag in tags:
        if tag in TAG_TO_SCHEMA_FILE:
            return TAG_TO_SCHEMA_FILE[tag]

    # 2. Try name pattern
    for pattern, domain in NAME_PATTERNS:
        if re.match(pattern, name, re.IGNORECASE):
            return domain

    # 3. Fallback
    return FALLBACK_DOMAIN


def classify_all(dry_run: bool = False) -> dict[str, list[str]]:
    """
    Classify all schemas from spec3.yaml into schemas/*.yaml files.
    Returns a dict of {domain: [schema names placed there]}.
    """
    spec = load_yaml(SPEC3_PATH)
    all_schemas: dict[str, Any] = spec.get("components", {}).get("schemas", {})

    if not all_schemas:
        print("No schemas found in spec3.yaml components/schemas")
        return {}

    # Build usage index (schema → tags of operations using it)
    usage_index = build_schema_usage_index(spec)

    # Load existing domain files
    domain_files: dict[str, dict[str, Any]] = {}
    for f in SCHEMAS_DIR.glob("*.yaml"):
        domain = f.stem
        existing = load_yaml(f)
        domain_files[domain] = existing.get("components", {}).get("schemas", {})

    # Classify each schema
    placements: dict[str, list[str]] = {}
    changes: list[str] = []

    for schema_name, schema_def in all_schemas.items():
        tags = usage_index.get(schema_name, set())
        domain = classify_schema(schema_name, tags)

        placements.setdefault(domain, []).append(schema_name)

        # Check if it differs from what's in the domain file
        current = domain_files.get(domain, {}).get(schema_name)
        if current is None:
            changes.append(f"+ {domain}/{schema_name}")
            print(f"  + [{domain}] {schema_name}  (new)")
        elif current != schema_def:
            changes.append(f"~ {domain}/{schema_name}")
            print(f"  ~ [{domain}] {schema_name}  (updated)")

        # Place schema in domain file (in memory)
        domain_files.setdefault(domain, {})[schema_name] = schema_def

    # Write updated domain files
    if not dry_run:
        for domain, schemas in domain_files.items():
            if not schemas:
                continue
            out_path = SCHEMAS_DIR / f"{domain}.yaml"
            save_yaml(out_path, {"components": {"schemas": schemas}})
            print(f"  Written: schemas/{domain}.yaml ({len(schemas)} schemas)")

    print(f"\nTotal schemas classified: {len(all_schemas)}")
    print(f"Changes: {len(changes)}")
    for domain, names in sorted(placements.items()):
        print(f"  {domain}.yaml: {len(names)} schemas")

    return placements


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Classify spec3.yaml schemas → schemas/*.yaml domain files"
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="Show classification without writing files")
    args = parser.parse_args()

    print(f"{'='*60}")
    print(f"Classifying schemas from spec3.yaml → schemas/*.yaml")
    print(f"{'='*60}\n")

    classify_all(dry_run=args.dry_run)

    if args.dry_run:
        print("\nDry run — no files written.")


if __name__ == "__main__":
    main()
