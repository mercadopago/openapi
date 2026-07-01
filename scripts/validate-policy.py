#!/usr/bin/env python3
"""
Validates the structure of apps.yaml.

Checks that each app entry has the required fields and that optional
fields (sdk, enabled) have the correct types.

pathPolicy (whitelist/blacklist/pending) is managed in Fury KVS and is
NOT validated here — use the bot's /openapi/policy/:app/pending endpoint
to check pending paths before merging.

Usage:
    python scripts/validate-policy.py
    python scripts/validate-policy.py --apps-file path/to/apps.yaml
"""

import sys
import argparse
import yaml

REQUIRED_FIELDS = ('fury_app', 'product', 'tag', 'paths')
VALID_SDK_LANGUAGES = ('python', 'java', 'node', 'php', 'ruby', 'go')
VALID_SITE_IDS = ('MLB', 'MLA', 'MLM', 'MLC', 'MCO', 'MPE', 'MLU')


def validate_app(app):
    """Validate a single app entry. Returns list of error strings."""
    errors = []
    fury_app = app.get('fury_app', '<unknown>')

    for field in REQUIRED_FIELDS:
        if not app.get(field):
            errors.append(f"[{fury_app}] missing required field '{field}'")

    paths = app.get('paths', [])
    if not isinstance(paths, list) or len(paths) == 0:
        errors.append(f"[{fury_app}] 'paths' must be a non-empty list")

    if 'enabled' in app and not isinstance(app['enabled'], bool):
        errors.append(f"[{fury_app}] 'enabled' must be a boolean (true/false)")

    for i, sdk in enumerate(app.get('sdk', [])):
        if not isinstance(sdk, dict):
            errors.append(f"[{fury_app}] sdk[{i}] must be an object")
            continue
        if sdk.get('language') not in VALID_SDK_LANGUAGES:
            errors.append(
                f"[{fury_app}] sdk[{i}].language '{sdk.get('language')}' is not valid. "
                f"Use one of: {', '.join(VALID_SDK_LANGUAGES)}"
            )
        if sdk.get('site_id') not in VALID_SITE_IDS:
            errors.append(
                f"[{fury_app}] sdk[{i}].site_id '{sdk.get('site_id')}' is not valid. "
                f"Use one of: {', '.join(VALID_SITE_IDS)}"
            )
        if 'target_repo' in sdk:
            errors.append(
                f"[{fury_app}] sdk[{i}].target_repo must not be in apps.yaml — "
                f"use GitHub Secret SDK_REPO_{sdk.get('language', 'LANG').upper()}"
            )
        if 'pathPolicy' in app:
            errors.append(
                f"[{fury_app}] 'pathPolicy' must not be in apps.yaml — "
                f"it is managed in Fury KVS via the bot policy endpoints"
            )

    return errors


def main():
    parser = argparse.ArgumentParser(description='Validate apps.yaml structure')
    parser.add_argument('--apps-file', default='apps.yaml', help='Path to apps.yaml')
    args = parser.parse_args()

    try:
        with open(args.apps_file, encoding='utf-8') as f:
            config = yaml.safe_load(f)
    except FileNotFoundError:
        print(f'❌ File not found: {args.apps_file}')
        sys.exit(1)
    except yaml.YAMLError as e:
        print(f'❌ Invalid YAML in {args.apps_file}: {e}')
        sys.exit(1)

    apps = config.get('apps', [])
    if not apps:
        print(f'❌ No apps found in {args.apps_file}')
        sys.exit(1)

    all_errors = []
    for app in apps:
        all_errors.extend(validate_app(app))

    if all_errors:
        print(f'❌ {len(all_errors)} validation error(s) in {args.apps_file}:\n')
        for err in all_errors:
            print(f'  {err}')
        sys.exit(1)

    print(f'✅ {len(apps)} app(s) validated successfully')
    sys.exit(0)


if __name__ == '__main__':
    main()
