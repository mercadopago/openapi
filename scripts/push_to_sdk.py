#!/usr/bin/env python3
"""
Trigger SDK generation for products that changed after a merge.

Reads by-product/{product}/diff.yaml and the full spec3.yaml,
transforms the diff into the SDK generator API format, and POSTs
to the spec-agent-generator service.

Only products with sdk configuration in apps.yaml are processed.

Usage:
    # Trigger for specific apps (comma-separated fury_app names)
    python scripts/push_to_sdk.py --apps payments,customers

    # Trigger for all apps that have SDK config in apps.yaml
    python scripts/push_to_sdk.py --all

    # Dry run — show what would be sent without HTTP calls
    python scripts/push_to_sdk.py --all --dry-run

Environment variables (set as GitHub secrets):
    SDK_GENERATOR_URL   Base URL of the spec-agent-generator service
                        e.g. https://spec-agent-generator-test.melioffice.com
    SDK_GENERATOR_TOKEN Bearer token for authentication (if required)
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
APPS_CONFIG_PATH = ROOT / "apps.yaml"
BY_PRODUCT = ROOT / "by-product"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SDK_GENERATOR_URL = os.environ.get(
    "SDK_GENERATOR_URL",
    "https://spec-agent-generator-test.melioffice.com",
)
SDK_GENERATOR_TOKEN = os.environ.get("SDK_GENERATOR_TOKEN", "")
SDK_GENERATOR_ENDPOINT = "/spec-agent/generate"
SPEC_VERSION = "1"


# ---------------------------------------------------------------------------
# Diff → SDK changes transformer
# ---------------------------------------------------------------------------

def _describe_path_from_spec(api_path: str, spec: dict[str, Any]) -> str:
    """Build a human-readable detail string for a path from the spec."""
    methods = spec.get("paths", {}).get(api_path, {})
    method_list = [m.upper() for m in methods if m in ("get", "post", "put", "patch", "delete")]
    return f"Path {api_path} with methods: {', '.join(method_list) or 'unknown'}."


def _describe_schema_from_spec(schema_name: str, spec: dict[str, Any]) -> str:
    """Build a human-readable detail string for a schema from the spec."""
    schema = spec.get("components", {}).get("schemas", {}).get(schema_name, {})
    props = list(schema.get("properties", {}).keys())
    required = schema.get("required", [])
    parts = []
    if props:
        parts.append(f"Properties: {', '.join(props[:10])}{'...' if len(props) > 10 else ''}.")
    if required:
        parts.append(f"Required: {', '.join(required)}.")
    return f"Create {schema_name} model. {' '.join(parts)}".strip()


def build_sdk_changes(diff: dict[str, Any], spec: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Transform a diff.yaml document into the SDK generator changes[] format.

    Mapping:
      paths.added   → change_type: new_endpoint
      paths.modified → change_type: modify
      paths.removed  → skipped (SDK generator does not handle removals)
      schemas.added  → change_type: new_endpoint (new model)
      schemas.modified → change_type: modify
    """
    changes = []

    for api_path in diff.get("paths", {}).get("added", []):
        changes.append({
            "change_type": "new_endpoint",
            "title": f"Add {api_path}",
            "detail": _describe_path_from_spec(api_path, spec),
            "affected_endpoints": [api_path],
        })

    for api_path in diff.get("paths", {}).get("modified", []):
        changes.append({
            "change_type": "modify",
            "title": f"Update {api_path}",
            "detail": f"Path {api_path} was modified. Review and update the SDK accordingly.",
            "affected_endpoints": [api_path],
        })

    for schema_name in diff.get("schemas", {}).get("added", []):
        changes.append({
            "change_type": "new_endpoint",
            "title": f"Add {schema_name} schema",
            "detail": _describe_schema_from_spec(schema_name, spec),
        })

    for schema_name in diff.get("schemas", {}).get("modified", []):
        changes.append({
            "change_type": "modify",
            "title": f"Update {schema_name} schema",
            "detail": f"Schema {schema_name} was modified. Update the SDK model accordingly.",
        })

    return changes


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------

def _http_post(url: str, payload: dict[str, Any], dry_run: bool) -> bool:
    if dry_run:
        print(f"  [dry-run] Would POST → {url}")
        print(f"  [dry-run] Payload: {len(payload.get('changes', []))} change(s)")
        return True

    try:
        import requests  # noqa: PLC0415
    except ImportError:
        print("  ERROR: 'requests' not installed. Add it to requirements.txt.", file=sys.stderr)
        return False

    headers = {"Content-Type": "application/json"}
    if SDK_GENERATOR_TOKEN:
        headers["Authorization"] = f"Bearer {SDK_GENERATOR_TOKEN}"

    print(f"  POST {url}")
    resp = requests.post(url, json=payload, headers=headers, timeout=60)
    if resp.ok:
        print(f"  ✅ {resp.status_code}")
        return True

    print(f"  ❌ {resp.status_code} — {resp.text[:200]}", file=sys.stderr)
    return False


# ---------------------------------------------------------------------------
# Per-product send
# ---------------------------------------------------------------------------

def send_sdk_generation(
    product: str,
    sdk_configs: list[dict[str, Any]],
    by_product_dir: Path,
    dry_run: bool = False,
) -> bool:
    """
    Trigger SDK generation for all SDK targets configured for *product*.
    Returns True if all targets succeed (or if there are no targets).
    """
    diff_path = by_product_dir / product / "diff.yaml"
    spec_path = by_product_dir / product / "spec3.yaml"

    if not spec_path.exists():
        print(f"\n  [{product}] spec3.yaml not found — skipping")
        return True

    if not diff_path.exists():
        print(f"\n  [{product}] diff.yaml not found — no changes to send")
        return True

    diff = yaml.safe_load(diff_path.read_text()) or {}
    spec = yaml.safe_load(spec_path.read_text()) or {}
    changes = build_sdk_changes(diff, spec)

    if not changes:
        print(f"\n  [{product}] No actionable changes for SDK generation — skipping")
        return True

    url = f"{SDK_GENERATOR_URL.rstrip('/')}{SDK_GENERATOR_ENDPOINT}"
    success = True

    for sdk_cfg in sdk_configs:
        language = sdk_cfg.get("language", "python")
        site_id = sdk_cfg.get("site_id", "MLB")
        target_repo = sdk_cfg.get("target_repo", "")

        print(f"\n  [{product}] → {language} / {site_id} — {len(changes)} change(s)")

        payload = {
            "spec_version": SPEC_VERSION,
            "language": language,
            "site_id": site_id,
            "target_repo": target_repo,
            "changes": changes,
        }

        ok = _http_post(url, payload, dry_run)
        if not ok:
            success = False

    return success


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def resolve_targets(
    fury_app_names: list[str],
    apps_config: list[dict[str, Any]],
) -> list[tuple[str, list[dict[str, Any]]]]:
    """
    Map fury_app names → (product_slug, sdk_configs[]).
    Only includes apps that have sdk[] entries in apps.yaml.
    """
    lookup = {a["fury_app"]: a for a in apps_config}
    targets = []

    for name in fury_app_names:
        app = lookup.get(name)
        if not app:
            print(f"  Warning: fury_app '{name}' not found in apps.yaml — skipping")
            continue
        sdk_configs = app.get("sdk", [])
        if not sdk_configs:
            continue
        product = app.get("product", name)
        targets.append((product, sdk_configs))

    return targets


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Trigger SDK generation for changed products"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--apps",
        metavar="APP1,APP2,...",
        help="Comma-separated fury_app names",
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Process all apps that have sdk config in apps.yaml",
    )
    parser.add_argument(
        "--openapi-path",
        default=str(ROOT),
        help="Path to the openapi repo root",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be sent without making HTTP calls",
    )
    args = parser.parse_args()

    openapi_root = Path(args.openapi_path).resolve()
    apps_cfg_path = openapi_root / "apps.yaml"
    by_product_dir = openapi_root / "by-product"

    with open(apps_cfg_path) as f:
        apps_config = yaml.safe_load(f).get("apps", [])

    if args.all:
        fury_app_names = [a["fury_app"] for a in apps_config]
    else:
        fury_app_names = [n.strip() for n in args.apps.split(",") if n.strip()]

    targets = resolve_targets(fury_app_names, apps_config)

    if not targets:
        print("No products with SDK configuration found.")
        sys.exit(0)

    print(f"\n{'='*60}")
    print(f"Triggering SDK generation for {len(targets)} product(s)")
    print(f"Generator: {SDK_GENERATOR_URL}{SDK_GENERATOR_ENDPOINT}")
    if args.dry_run:
        print("[DRY RUN — no HTTP calls will be made]")
    print(f"{'='*60}")

    failures = 0
    for product, sdk_configs in targets:
        ok = send_sdk_generation(product, sdk_configs, by_product_dir, dry_run=args.dry_run)
        if not ok:
            failures += 1

    print(f"\n{'='*60}")
    print(f"Done. {len(targets) - failures}/{len(targets)} product(s) triggered successfully.")

    if failures:
        sys.exit(1)


if __name__ == "__main__":
    main()
