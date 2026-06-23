#!/usr/bin/env python3
"""
Push spec changes from openapi/spec3.yaml → devsite-docs/reference/api-json/.

Reads spec3.yaml, extracts paths per app (from apps.yaml), and updates
the corresponding api-json files in devsite-docs.

Rules for merging into api-json:
  UPDATE:  description.en, parameters (schema + description.en), requestBody schema
  KEEP:    description.pt, description.es (translations untouched)
  KEEP:    java, php, js, python, csharp, ruby code examples
  KEEP:    sdks, tags (MP product tags, different from OpenAPI tags)
  CREATE:  new endpoints get a skeleton with description.en and TODO markers for pt/es

Usage:
    # From the openapi repo root, with devsite-docs checked out at ../devsite-docs
    python scripts/push_to_devsite.py --app payments
    python scripts/push_to_devsite.py --all
    python scripts/push_to_devsite.py --all --dry-run

    # Custom paths (used by GitHub Actions where repos are checked out separately)
    python scripts/push_to_devsite.py --app payments \
        --devsite-path /workspace/devsite-docs \
        --openapi-path /workspace/openapi
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# Default ROOT is the repo root (resolved at runtime, overridable via --openapi-path)
_DEFAULT_ROOT = Path(__file__).resolve().parent.parent

SDK_KEYS = {"java", "php", "js", "python", "csharp", "ruby"}
I18N_LANGS = ("en", "pt", "es")
DEVSITE_REPO = os.environ.get("DEVSITE_REPO", "melisource/fury_devsite-docs")


# ---------------------------------------------------------------------------
# Loaders
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def load_json(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return json.load(f)


def save_json(path: Path, data: dict[str, Any]) -> None:
    with open(path, "w") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
        f.write("\n")


# ---------------------------------------------------------------------------
# $ref resolver — inlines components/schemas references
# ---------------------------------------------------------------------------

def resolve_refs(obj: Any, schemas: dict[str, Any], _depth: int = 0) -> Any:
    """Recursively resolve $ref pointers using components/schemas."""
    if _depth > 10:
        return obj
    if isinstance(obj, dict):
        if "$ref" in obj:
            ref = obj["$ref"]
            if ref.startswith("#/components/schemas/"):
                name = ref.split("/")[-1]
                resolved = schemas.get(name, {})
                return resolve_refs(copy.deepcopy(resolved), schemas, _depth + 1)
            return obj
        return {k: resolve_refs(v, schemas, _depth + 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [resolve_refs(item, schemas, _depth + 1) for item in obj]
    return obj


# ---------------------------------------------------------------------------
# i18n description helpers
# ---------------------------------------------------------------------------

def _as_i18n(value: Any) -> dict[str, str]:
    """
    Coerce a value to an i18n dict.
    - If already {"en": ..., "pt": ..., "es": ...} — return as-is.
    - If plain string — wrap as {"en": value, "pt": "TODO", "es": "TODO"}.
    """
    if isinstance(value, dict) and "en" in value:
        return value
    text = str(value) if value else ""
    return {"en": text, "pt": "TODO", "es": "TODO"}


def _merge_description(existing: Any, new_en: str) -> dict[str, str]:
    """
    Update the English description while preserving existing pt/es translations.
    """
    if isinstance(existing, dict):
        result = dict(existing)
    else:
        result = {"pt": "TODO", "es": "TODO"}
    result["en"] = new_en or result.get("en", "")
    return result


# ---------------------------------------------------------------------------
# Parameter conversion: OpenAPI 3.1 → api-json format
# ---------------------------------------------------------------------------

def _convert_parameter(oa_param: dict[str, Any], existing_param: dict[str, Any] | None) -> dict[str, Any]:
    """
    Convert an OpenAPI parameter to api-json parameter format.
    Preserves existing pt/es translations.
    """
    result: dict[str, Any] = {
        "in": oa_param.get("in", "query"),
        "name": oa_param.get("name", ""),
        "required": oa_param.get("required", False),
    }

    # schema
    if "schema" in oa_param:
        result["schema"] = oa_param["schema"]

    # description — merge with existing translations
    raw_desc = oa_param.get("description", "")
    if isinstance(raw_desc, dict):
        new_en = raw_desc.get("en", "")
    else:
        new_en = str(raw_desc) if raw_desc else ""

    existing_desc = (existing_param or {}).get("description", {})
    result["description"] = _merge_description(existing_desc, new_en)

    return result


def _merge_parameters(
    oa_params: list[dict[str, Any]],
    existing_params: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Merge OpenAPI parameters list into existing api-json parameters.
    Matches by (in, name). Adds new, updates existing, keeps untouched ones.
    """
    existing_index: dict[tuple[str, str], dict[str, Any]] = {
        (p.get("in", ""), p.get("name", "")): p
        for p in (existing_params or [])
    }
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for oa_p in oa_params:
        key = (oa_p.get("in", ""), oa_p.get("name", ""))
        existing = existing_index.get(key)
        result.append(_convert_parameter(oa_p, existing))
        seen.add(key)

    # Keep existing params not present in openapi (manually added)
    for key, param in existing_index.items():
        if key not in seen:
            result.append(param)

    return result


# ---------------------------------------------------------------------------
# requestBody conversion
# ---------------------------------------------------------------------------

def _convert_request_body(
    oa_request_body: dict[str, Any],
    schemas: dict[str, Any],
) -> dict[str, Any]:
    """
    Convert OpenAPI requestBody to api-json requestBody format.
    Resolves $refs inline so devsite gets flat schemas.
    """
    resolved = resolve_refs(copy.deepcopy(oa_request_body), schemas)
    return resolved


# ---------------------------------------------------------------------------
# Operation merge: one HTTP method on one path
# ---------------------------------------------------------------------------

def _merge_operation(
    oa_op: dict[str, Any],
    existing_op: dict[str, Any] | None,
    schemas: dict[str, Any],
) -> dict[str, Any]:
    """
    Merge an OpenAPI operation into an existing api-json operation.
    """
    if existing_op is None:
        # New endpoint — create skeleton, preserve no existing data
        result: dict[str, Any] = {
            "sdks": False,
            "tags": [],
            "description": _as_i18n(oa_op.get("description") or oa_op.get("summary", "")),
        }
    else:
        result = copy.deepcopy(existing_op)

    # Always update description.en
    new_desc = oa_op.get("description") or oa_op.get("summary", "")
    if isinstance(new_desc, dict):
        new_en = new_desc.get("en", "")
    else:
        new_en = str(new_desc) if new_desc else ""

    result["description"] = _merge_description(result.get("description", {}), new_en)

    # Parameters
    if "parameters" in oa_op:
        result["parameters"] = _merge_parameters(
            oa_op["parameters"],
            result.get("parameters", []),
        )

    # requestBody
    if "requestBody" in oa_op:
        result["requestBody"] = _convert_request_body(oa_op["requestBody"], schemas)

    # responses
    if "responses" in oa_op:
        result["responses"] = resolve_refs(copy.deepcopy(oa_op["responses"]), schemas)

    return result


# ---------------------------------------------------------------------------
# Main merge per app
# ---------------------------------------------------------------------------

def merge_into_api_json(
    app_config: dict[str, Any],
    spec3: dict[str, Any],
    api_json: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """
    Merge spec3.yaml paths (scoped to app_config) into an api-json dict.
    Returns (updated_api_json, changes).
    """
    allowed_prefixes: list[str] = app_config.get("paths", [])
    schemas: dict[str, Any] = spec3.get("components", {}).get("schemas", {})
    spec_paths: dict[str, Any] = spec3.get("paths", {})

    result = copy.deepcopy(api_json)
    result.setdefault("url", "https://api.mercadopago.com")
    result.setdefault("paths", {})

    changes: list[str] = []

    for path, methods in spec_paths.items():
        if allowed_prefixes and not any(path.startswith(p) for p in allowed_prefixes):
            continue

        if path not in result["paths"]:
            result["paths"][path] = {}

        for method, oa_op in methods.items():
            if not isinstance(oa_op, dict):
                continue

            existing_op = result["paths"][path].get(method)
            updated_op = _merge_operation(oa_op, existing_op, schemas)

            if existing_op is None:
                result["paths"][path][method] = updated_op
                changes.append(f"+ {method.upper()} {path}")
                print(f"  + {method.upper()} {path}")
            elif updated_op != existing_op:
                result["paths"][path][method] = updated_op
                changes.append(f"~ {method.upper()} {path}")
                print(f"  ~ {method.upper()} {path}")

    return result, changes


# ---------------------------------------------------------------------------
# Git helpers for cross-repo push
# ---------------------------------------------------------------------------

def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd), capture_output=True, text=True
    )
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


def push_branch_to_devsite(
    devsite_path: Path,
    app_name: str,
    changes: list[str],
    changed_files: list[str],
) -> str:
    """Commit changes in devsite_path and push to a new branch. Returns branch name."""
    content_hash = hashlib.sha256("|".join(changes).encode()).hexdigest()[:8]
    branch = f"enhancement/automatic-spec-{app_name}/{app_name}-{content_hash}"

    git("checkout", "-b", branch, cwd=devsite_path)
    for f in changed_files:
        git("add", f, cwd=devsite_path)

    commit_msg = (
        f"chore(spec): sync {app_name} from openapi/spec3.yaml\n\n"
        + "\n".join(f"- {c}" for c in changes)
    )
    git("commit", "-m", commit_msg, cwd=devsite_path)
    git("push", "origin", branch, cwd=devsite_path)

    return branch


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push spec3.yaml changes → devsite-docs api-json files"
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--app", metavar="APP_NAME", help="Fury app name to sync")
    group.add_argument("--all", action="store_true", help="Sync all apps in apps.yaml")
    parser.add_argument(
        "--openapi-path",
        default=str(_DEFAULT_ROOT),
        help="Path to the openapi repo root (default: parent of this script)",
    )
    parser.add_argument(
        "--devsite-path",
        default=str(_DEFAULT_ROOT.parent / "devsite-docs"),
        help="Path to devsite-docs repo (default: ../devsite-docs)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show changes, no write/push")
    args = parser.parse_args()

    # Resolve paths dynamically so GitHub Actions (separate checkouts) works correctly
    openapi_root   = Path(args.openapi_path).resolve()
    spec3_path     = openapi_root / "spec3.yaml"
    apps_cfg_path  = openapi_root / "apps.yaml"

    devsite_path = Path(args.devsite_path).resolve()
    api_json_dir = devsite_path / "reference" / "api-json"

    if not api_json_dir.exists():
        print(f"Error: {api_json_dir} does not exist.", file=sys.stderr)
        print("Check --devsite-path or ensure devsite-docs is checked out.", file=sys.stderr)
        sys.exit(1)

    apps_config: list[dict[str, Any]] = load_yaml(apps_cfg_path).get("apps", [])
    spec3: dict[str, Any] = load_yaml(spec3_path)

    if args.app:
        matched = [a for a in apps_config if a["fury_app"] == args.app]
        if not matched:
            print(f"App '{args.app}' not found in apps.yaml", file=sys.stderr)
            sys.exit(1)
        apps_to_sync = matched
    else:
        apps_to_sync = apps_config

    all_changes: list[str] = []
    all_changed_files: list[str] = []

    for app_config in apps_to_sync:
        app_name = app_config["fury_app"]
        devsite_file = app_config.get("product", app_name)
        json_path = api_json_dir / f"{devsite_file}.json"

        print(f"\n{'='*60}")
        print(f"Syncing: {app_name} → {json_path.name}")
        print(f"{'='*60}")

        if json_path.exists():
            api_json = load_json(json_path)
        else:
            print(f"  File not found — will create: {json_path.name}")
            api_json = {"url": "https://api.mercadopago.com", "paths": {}}

        updated, changes = merge_into_api_json(app_config, spec3, api_json)

        if not changes:
            print("  No changes.")
            continue

        print(f"  {len(changes)} change(s).")
        all_changes.extend(changes)

        if not args.dry_run:
            save_json(json_path, updated)
            all_changed_files.append(str(json_path.relative_to(devsite_path)))
            print(f"  Written: {json_path.name}")

    print(f"\n{'='*60}")
    print(f"Total changes: {len(all_changes)}")

    if args.dry_run:
        print("Dry run — no files written, no branch pushed.")
        return

    if not all_changes:
        print("Nothing to push.")
        return

    # One branch per app, or a single multi-app branch
    if len(apps_to_sync) == 1:
        branch = push_branch_to_devsite(
            devsite_path,
            apps_to_sync[0]["fury_app"],
            all_changes,
            all_changed_files,
        )
    else:
        branch = push_branch_to_devsite(
            devsite_path, "multi", all_changes, all_changed_files
        )

    print(f"\nBranch pushed to devsite-docs: {branch}")
    print("The pr-on-push.yml workflow in devsite-docs will create the PR automatically.")


if __name__ == "__main__":
    main()
