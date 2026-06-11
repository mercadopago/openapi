#!/usr/bin/env python3
"""
Push spec3.sdk.yaml to the MercadoPago SDK repositories.

Reads openapi/spec3.sdk.yaml (the SDK-flavored spec with x-mp-sdk-coverage
extensions) and pushes it to the configured SDK repo so each SDK's CI can
regenerate client code and documentation from the updated spec.

Usage:
    python scripts/push_to_sdk.py
    python scripts/push_to_sdk.py --dry-run         # show diff, no push
    python scripts/push_to_sdk.py --sdk-path /path  # custom SDK repo path

Environment variables:
    GH_PAT               GitHub Personal Access Token (write on SDK repo)
    SDK_REPO_OWNER       GitHub org (default: mercadopago)
    SDK_REPO_NAME        SDK repo name (default: sdk-java — TODO: confirm)
    SDK_REPO_BASE_BRANCH Base branch in SDK repo (default: main)
    SDK_SPEC_PATH        Path inside SDK repo where the spec lives
                         (default: openapi/spec3.yaml)
"""

from __future__ import annotations

import argparse
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

ROOT        = Path(__file__).resolve().parent.parent
SPEC_SDK    = ROOT / "spec3.sdk.yaml"          # SDK-flavored spec
APPS_CONFIG = ROOT / "apps.yaml"

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SDK_REPO_OWNER       = os.environ.get("SDK_REPO_OWNER", "mercadopago")
SDK_REPO_NAME        = os.environ.get("SDK_REPO_NAME", "sdk-java")   # TODO: confirm
SDK_REPO_BASE_BRANCH = os.environ.get("SDK_REPO_BASE_BRANCH", "main")
SDK_SPEC_PATH        = os.environ.get("SDK_SPEC_PATH", "openapi/spec3.yaml")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False,
                  default_flow_style=False, width=120)


def git(*args: str, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=str(cwd),
                            capture_output=True, text=True)
    if result.returncode != 0:
        print(f"git {' '.join(args)} failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return result.stdout.strip()


# ---------------------------------------------------------------------------
# Core: detect changes between current spec3.sdk.yaml and the one in SDK repo
# ---------------------------------------------------------------------------

def specs_differ(local_spec: dict[str, Any], remote_content: str) -> bool:
    """Return True if local spec differs from what's in the SDK repo."""
    try:
        remote_spec = yaml.safe_load(remote_content)
        return json.dumps(local_spec, sort_keys=True) != json.dumps(remote_spec, sort_keys=True)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Push to SDK repo
# ---------------------------------------------------------------------------

def push_to_sdk_repo(sdk_repo_path: Path, spec_content: str, dry_run: bool) -> bool:
    """
    Copy spec3.sdk.yaml into the SDK repo, create a branch and push.
    Returns True if there were changes.
    """
    target_file = sdk_repo_path / SDK_SPEC_PATH

    # Read current file in SDK repo
    if target_file.exists():
        current = target_file.read_text()
        if current == spec_content:
            print("  No changes in SDK spec — skipping.")
            return False

    if dry_run:
        print(f"  [dry-run] Would update {SDK_SPEC_PATH} in {SDK_REPO_OWNER}/{SDK_REPO_NAME}")
        return True

    # Write updated spec
    target_file.parent.mkdir(parents=True, exist_ok=True)
    target_file.write_text(spec_content)

    # Create branch + commit + push
    content_hash = hashlib.sha256(spec_content.encode()).hexdigest()[:8]
    branch = f"enhancement/automatic-spec-openapi/spec3-sdk-{content_hash}"

    git("checkout", "-b", branch, cwd=sdk_repo_path)
    git("add", SDK_SPEC_PATH, cwd=sdk_repo_path)
    git("commit", "-m",
        f"chore(spec): update SDK spec from openapi/spec3.sdk.yaml\n\n"
        f"Auto-generated from fury_openapi repo.\n"
        f"Spec hash: {content_hash}",
        cwd=sdk_repo_path)
    git("push", "origin", branch, cwd=sdk_repo_path)

    print(f"  Branch pushed: {branch}")
    print(f"  → pr-on-push.yml in {SDK_REPO_NAME} will create the PR automatically")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Push spec3.sdk.yaml to MercadoPago SDK repositories"
    )
    parser.add_argument(
        "--sdk-path",
        default=str(ROOT.parent / SDK_REPO_NAME),
        help=f"Path to SDK repo (default: ../{SDK_REPO_NAME})",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Show what would change without pushing"
    )
    args = parser.parse_args()

    sdk_path = Path(args.sdk_path).resolve()

    if not SPEC_SDK.exists():
        print(f"Error: {SPEC_SDK} not found.", file=sys.stderr)
        print("Run 'python scripts/bundle.py' first to generate spec3.sdk.yaml", file=sys.stderr)
        sys.exit(1)

    if not sdk_path.exists() and not args.dry_run:
        print(f"Error: SDK repo not found at {sdk_path}", file=sys.stderr)
        print(f"Clone {SDK_REPO_OWNER}/{SDK_REPO_NAME} next to this repo first.", file=sys.stderr)
        sys.exit(1)

    spec_content = SPEC_SDK.read_text()

    print(f"\nPushing spec3.sdk.yaml → {SDK_REPO_OWNER}/{SDK_REPO_NAME}")
    print(f"  Spec path in SDK repo: {SDK_SPEC_PATH}")
    print(f"  Spec size: {len(spec_content)} chars")

    changed = push_to_sdk_repo(sdk_path, spec_content, args.dry_run)

    if not changed:
        print("Nothing to push — SDK spec is already up to date.")
    elif args.dry_run:
        print("Dry run complete — no files pushed.")
    else:
        print("Done.")


if __name__ == "__main__":
    main()
