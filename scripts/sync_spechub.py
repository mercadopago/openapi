#!/usr/bin/env python3
"""
Sync OpenAPI specs from Fury Spec Hub directly into openapi/spec3.yaml.

Reads specs from Spec Hub, merges paths and generates/updates
components/schemas in spec3.yaml — no devsite-docs involved.

Usage:
    # Sync one app
    python scripts/sync_spechub.py --app payments

    # Sync all apps listed in apps.yaml
    python scripts/sync_spechub.py --all

    # Dry run (show diff, no file write)
    python scripts/sync_spechub.py --all --dry-run

Environment variables (set as GitHub secrets):
    SPEC_HUB_BASE_URL  Base URL of the Fury Spec Hub REST API
                       e.g. https://spechub.furycloud.io/api/v1
                       TODO: confirm with the Fury platform team
    FURY_TOKEN         Bearer token for Spec Hub authentication
                       (service account or personal Fury token)
"""

from __future__ import annotations

import argparse
import copy
import difflib
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import Any

import requests
import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
SPEC3_PATH = ROOT / "spec3.yaml"
APPS_CONFIG_PATH = ROOT / "apps.yaml"

# ---------------------------------------------------------------------------
# Spec Hub client
# ---------------------------------------------------------------------------

# The Fury Spec Hub lives at Document Hubs (http://document-hubs.melisystems.com),
# which is an INTERNAL Meli endpoint — not reachable from GitHub Actions.
#
# The Fury MCP server (https://fury-mcp.melioffice.com/mcp/) IS externally accessible
# and wraps the internal Document Hubs API. We call it directly using the MCP
# JSON-RPC protocol over HTTP, exactly as Claude does interactively.
#
# Auth: set FURY_TOKEN to a Fury/Meli user token or service account token.
# You can generate one at https://web.furycloud.io or with the Fury CLI:
#   fury token create --scope read

FURY_MCP_URL = os.environ.get(
    "FURY_MCP_URL",
    "https://fury-mcp.melioffice.com/mcp/",
)
FURY_TOKEN = os.environ.get("FURY_TOKEN", "")


def fetch_spec(app_name: str) -> dict[str, Any]:
    """
    Fetch the OpenAPI spec for *app_name* by calling the Fury MCP server
    via the MCP JSON-RPC HTTP protocol.

    This avoids the need to know the internal Document Hubs REST API URL.
    The MCP server at fury-mcp.melioffice.com is publicly reachable and
    handles auth + routing to the internal spec store.
    """
    if not FURY_TOKEN:
        raise SystemExit(
            "FURY_TOKEN env var is required.\n"
            "Generate a Fury token at https://web.furycloud.io\n"
            "or with: fury token create --scope read\n"
            "Then add it as a GitHub Actions secret named FURY_TOKEN."
        )

    payload = {
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "search_api_specs",
            "arguments": {"app_name": app_name},
        },
        "id": 1,
    }

    headers = {
        "Authorization": f"Bearer {FURY_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
    }

    resp = requests.post(FURY_MCP_URL, json=payload, headers=headers, timeout=60)
    resp.raise_for_status()

    # MCP streamable-HTTP may respond as SSE or plain JSON depending on Accept header.
    # With Accept: application/json the server returns a plain JSON-RPC response.
    body = resp.json()

    if "error" in body:
        raise SystemExit(
            f"MCP error fetching spec for '{app_name}': {body['error']}"
        )

    # Extract the text content from the MCP tool result
    content_blocks = body.get("result", {}).get("content", [])
    spec_text = next(
        (block["text"] for block in content_blocks if block.get("type") == "text"),
        None,
    )

    if not spec_text or "No specs found" in spec_text:
        raise SystemExit(
            f"No spec found in Fury MCP for app '{app_name}'.\n"
            f"MCP response: {spec_text}\n\n"
            "Check that the app name in apps.yaml exactly matches\n"
            "the name registered in Fury (fury app list or web.furycloud.io)."
        )

    # Spec Hub returns YAML or JSON — handle both
    if spec_text.lstrip().startswith("{"):
        return json.loads(spec_text)
    return yaml.safe_load(spec_text)


# ---------------------------------------------------------------------------
# Schema extraction and merging
# ---------------------------------------------------------------------------

def _collect_inline_schemas(
    obj: Any,
    parent_key: str,
    collected: dict[str, Any],
    depth: int = 0,
) -> Any:
    """
    Walk an OpenAPI object tree and hoist inline object/array schemas into
    collected, returning a $ref in their place.

    Only hoists named objects (properties with 'type: object') at depth > 0
    to avoid collisions.
    """
    if not isinstance(obj, dict):
        return obj

    # If already a $ref, keep it
    if "$ref" in obj:
        return obj

    keys = set(obj.keys())

    # Process children first (bottom-up)
    result: dict[str, Any] = {}
    for k, v in obj.items():
        if k == "properties" and isinstance(v, dict):
            result[k] = {
                prop: _collect_inline_schemas(schema, prop, collected, depth + 1)
                for prop, schema in v.items()
            }
        elif k in ("items", "additionalProperties") and isinstance(v, dict):
            result[k] = _collect_inline_schemas(v, parent_key + "Item", collected, depth + 1)
        elif k == "allOf" and isinstance(v, list):
            result[k] = [
                _collect_inline_schemas(item, parent_key, collected, depth + 1)
                for item in v
            ]
        else:
            result[k] = v

    # Hoist this object into collected if it's a named object schema at depth > 0
    if (
        depth > 0
        and result.get("type") == "object"
        and "properties" in result
        and parent_key
    ):
        name = _pascal(parent_key)
        if name not in collected:
            collected[name] = copy.deepcopy(result)
        return {"$ref": f"#/components/schemas/{name}"}

    return result


def _pascal(name: str) -> str:
    """Convert snake_case or camelCase to PascalCase."""
    import re
    parts = re.split(r"[_\-\s]+", name)
    return "".join(p.capitalize() for p in parts) if parts else name


def _convert_30_to_31(schema: dict[str, Any]) -> dict[str, Any]:
    """
    Shallow conversion of OpenAPI 3.0 constructs to 3.1.
    Only handles the most common differences.
    """
    if not isinstance(schema, dict):
        return schema

    result = {}
    for k, v in schema.items():
        if k == "nullable" and v is True:
            # Skip — will be handled by type conversion below
            continue
        result[k] = v

    # nullable: true + type: T  →  type: [T, "null"]
    if schema.get("nullable") is True:
        t = schema.get("type")
        if t and isinstance(t, str):
            result["type"] = [t, "null"]
        elif "type" not in schema:
            result["type"] = "null"

    # Recurse into children
    for k in ("properties", "items", "additionalProperties"):
        if k in result and isinstance(result[k], dict):
            if k == "properties":
                result[k] = {
                    pk: _convert_30_to_31(pv)
                    for pk, pv in result[k].items()
                }
            else:
                result[k] = _convert_30_to_31(result[k])

    return result


def extract_schemas(incoming_spec: dict[str, Any]) -> dict[str, Any]:
    """
    Extract all schemas from incoming spec's components/schemas.
    Converts from OpenAPI 3.0 → 3.1 if needed.
    """
    raw = incoming_spec.get("components", {}).get("schemas", {})
    return {
        name: _convert_30_to_31(schema)
        for name, schema in raw.items()
    }


def extract_inline_schemas_from_paths(
    paths: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """
    Walk paths and extract any inline object schemas found in requestBody
    or response content. Returns (cleaned_paths, extracted_schemas).
    """
    collected: dict[str, Any] = {}
    cleaned = _collect_inline_schemas(paths, "", collected, depth=0)
    return cleaned, collected


# ---------------------------------------------------------------------------
# Spec3 merge logic
# ---------------------------------------------------------------------------

def merge_schemas(
    current: dict[str, Any],
    new_schemas: dict[str, Any],
) -> list[str]:
    """Merge new_schemas into current components/schemas. Returns change list."""
    changes: list[str] = []
    target = current.setdefault("components", {}).setdefault("schemas", {})

    for name, schema in new_schemas.items():
        if name not in target:
            target[name] = schema
            changes.append(f"+ schema: {name}")
            print(f"  + schema: {name}")
        elif target[name] != schema:
            target[name] = schema
            changes.append(f"~ schema: {name}")
            print(f"  ~ schema: {name}")

    return changes


def merge_paths(
    current: dict[str, Any],
    incoming_paths: dict[str, Any],
    allowed_prefixes: list[str],
) -> tuple[list[str], list[str]]:
    """
    Merge incoming_paths into current spec3.yaml paths.
    Only touches paths that match allowed_prefixes.
    Returns (changes, removals_detected).

    Removals are NOT applied automatically — they're returned so the caller
    can include them in the PR description for human review.
    """
    changes: list[str] = []
    removals: list[str] = []
    target = current.setdefault("paths", {})

    # Paths in spec3 owned by this app
    owned_in_spec = [
        p for p in target
        if allowed_prefixes and any(p.startswith(prefix) for prefix in allowed_prefixes)
    ]
    incoming_path_set = set(incoming_paths.keys())

    # Detect removed paths (exist in spec3 but gone from Spec Hub)
    for path in owned_in_spec:
        if path not in incoming_path_set:
            removals.append(f"? removed from Spec Hub: {path}")
            print(f"  ? no longer in Spec Hub: {path}  (keeping — manual review needed)")

    # Add / update
    for path, methods in incoming_paths.items():
        if allowed_prefixes and not any(
            path.startswith(prefix) for prefix in allowed_prefixes
        ):
            continue

        if path not in target:
            target[path] = methods
            changes.append(f"+ path: {path}")
            print(f"  + path: {path}")
        else:
            for method, operation in methods.items():
                if method not in target[path]:
                    target[path][method] = operation
                    changes.append(f"+ {method.upper()} {path}")
                    print(f"  + {method.upper()} {path}")
                elif target[path][method] != operation:
                    target[path][method] = operation
                    changes.append(f"~ {method.upper()} {path}")
                    print(f"  ~ {method.upper()} {path}")

            # Detect removed HTTP methods
            for method in list(target[path].keys()):
                if method not in methods:
                    removals.append(f"? removed from Spec Hub: {method.upper()} {path}")
                    print(f"  ? method no longer in Spec Hub: {method.upper()} {path}  (keeping)")

    return changes, removals


# ---------------------------------------------------------------------------
# Main sync logic
# ---------------------------------------------------------------------------

def load_apps_config() -> list[dict[str, Any]]:
    with open(APPS_CONFIG_PATH) as f:
        return yaml.safe_load(f).get("apps", [])


def load_spec3() -> dict[str, Any]:
    with open(SPEC3_PATH) as f:
        return yaml.safe_load(f)


def save_spec3(spec: dict[str, Any]) -> None:
    with open(SPEC3_PATH, "w") as f:
        yaml.dump(spec, f, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120)


def sync_app(
    app_config: dict[str, Any],
    current_spec: dict[str, Any],
    dry_run: bool = False,
) -> tuple[list[str], list[str]]:
    """
    Sync one Fury app from Spec Hub into current_spec.
    Returns (changes, removals_detected).
    Removals are never auto-applied — they appear in the PR for human review.
    """
    app_name = app_config["fury_app"]
    allowed_prefixes = app_config.get("paths", [])

    print(f"\n{'='*60}")
    print(f"Syncing app: {app_name}")
    print(f"{'='*60}")

    incoming = fetch_spec(app_name)
    print(f"  Fetched spec: OpenAPI {incoming.get('openapi', incoming.get('swagger', '?'))}")
    print(f"  Paths in spec: {len(incoming.get('paths', {}))}")

    top_schemas = extract_schemas(incoming)
    incoming_paths = incoming.get("paths", {})
    cleaned_paths, inline_schemas = extract_inline_schemas_from_paths(incoming_paths)
    all_new_schemas = {**top_schemas, **inline_schemas}

    print(f"  Schemas to merge: {len(all_new_schemas)}")
    print(f"  Paths to evaluate: {len(cleaned_paths)}")

    changes: list[str] = []
    removals: list[str] = []

    target = current_spec if not dry_run else copy.deepcopy(current_spec)
    changes += merge_schemas(target, all_new_schemas)
    path_changes, path_removals = merge_paths(target, cleaned_paths, allowed_prefixes)
    changes += path_changes
    removals += path_removals

    if not dry_run and target is not current_spec:
        # dry_run used a copy — don't mutate original
        pass

    return changes, removals


def print_diff(original_yaml: str, updated_spec: dict[str, Any]) -> None:
    updated_yaml = yaml.dump(
        updated_spec, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120
    )
    diff = list(difflib.unified_diff(
        original_yaml.splitlines(keepends=True),
        updated_yaml.splitlines(keepends=True),
        fromfile="spec3.yaml (before)",
        tofile="spec3.yaml (after)",
        n=3,
    ))
    if diff:
        print("\n--- Diff preview (first 100 lines) ---")
        print("".join(diff[:100]))
    else:
        print("\nNo diff.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Sync Fury Spec Hub → openapi/spec3.yaml")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--app", metavar="APP_NAME", help="Fury app name to sync")
    group.add_argument("--all", action="store_true", help="Sync all apps in apps.yaml")
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    apps_config = load_apps_config()

    if args.app:
        matched = [a for a in apps_config if a["fury_app"] == args.app]
        if not matched:
            # Allow syncing an app not in apps.yaml (with no path filtering)
            matched = [{"fury_app": args.app, "paths": []}]
        apps_to_sync = matched
    else:
        apps_to_sync = apps_config

    current_spec = load_spec3()
    original_yaml = yaml.dump(
        current_spec, allow_unicode=True, sort_keys=False, default_flow_style=False, width=120
    )

    all_changes: list[str] = []
    all_removals: list[str] = []

    for app_config in apps_to_sync:
        changes, removals = sync_app(app_config, current_spec, dry_run=args.dry_run)
        all_changes.extend(changes)
        all_removals.extend(removals)

    print(f"\n{'='*60}")
    print(f"Total changes : {len(all_changes)}")
    print(f"Total removals detected (NOT applied): {len(all_removals)}")

    if args.dry_run:
        print_diff(original_yaml, current_spec)
        print("\nDry run — spec3.yaml was NOT modified.")
        return

    if all_changes:
        save_spec3(current_spec)
        print("spec3.yaml updated.")
    else:
        print("No changes — spec3.yaml unchanged.")

    # Write removal warnings to a file so the workflow can include them in the PR body
    if all_removals:
        warnings_path = Path("sync-removal-warnings.txt")
        warnings_path.write_text(
            "## ⚠️ Endpoints no longer found in Fury Spec Hub\n\n"
            "These paths/methods exist in spec3.yaml but were **not returned** by "
            "Spec Hub on this sync. They were kept unchanged — review manually and "
            "remove if confirmed deprecated.\n\n"
            + "\n".join(f"- `{r.replace('? removed from Spec Hub: ', '')}`" for r in all_removals)
            + "\n"
        )
        print(f"\nRemoval warnings written to {warnings_path}")


if __name__ == "__main__":
    main()
