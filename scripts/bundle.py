#!/usr/bin/env python3
"""
Bundle OpenAPI specs: merge schema fragments + apply country overlays.

Two-phase pipeline:
  Phase 1 — Schema bundle:
    Read schemas/*.yaml fragments → merge into spec3.yaml components/schemas.
    spec3.yaml stays self-contained (zero external $ref).

  Phase 2 — By-site generation:
    Read spec3.yaml + overlays/{SITE}.yaml (OAL 1.0) →
    write by-site/{SITE}/spec3.yaml for each country.

Usage:
    python scripts/bundle.py              # full bundle: schemas + all sites
    python scripts/bundle.py --schemas-only        # only phase 1
    python scripts/bundle.py --sites-only          # only phase 2
    python scripts/bundle.py MLB MLA               # phase 2 for specific sites
    python scripts/bundle.py --check               # CI: exit 1 if anything is stale
"""

from __future__ import annotations

import argparse
import copy
import re
import sys
from pathlib import Path
from typing import Any

import yaml

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

ROOT        = Path(__file__).resolve().parent.parent
SPEC3_PATH  = ROOT / "spec3.yaml"
SCHEMAS     = ROOT / "schemas"
OVERLAYS    = ROOT / "overlays"
BY_SITE     = ROOT / "by-site"
BY_PRODUCT  = ROOT / "by-product"
APPS_CONFIG = ROOT / "apps.yaml"

# ---------------------------------------------------------------------------
# Per-country metadata injected into info.title / info.description / contact
# ---------------------------------------------------------------------------

COUNTRY_META: dict[str, dict[str, str]] = {
    "MLA": {
        "name":        "Argentina",
        "title":       "MercadoPago API — Argentina (MLA)",
        "contact_url": "https://www.mercadopago.com.ar/developers",
        "description": (
            "MercadoPago API specification scoped to Argentina (site_id: MLA).\n\n"
            "## Argentina-specific notes\n\n"
            "- **Currency**: ARS — send amounts as decimals (e.g., `5000.00`)\n\n"
            "- **Cash payments**: Rapipago and Pago Fácil are the main cash networks\n\n"
            "- **Identification**: `DNI` (individuals), `CUIL` / `CUIT` (tax IDs)\n\n"
            "- **Card brands**: Visa, Mastercard, Amex, Naranja, Cabal, Argencard\n\n"
            "- **Installments**: Widely used — up to 24 cuotas on credit cards\n\n"
            "- **Point**: Available in Argentina\n\n"
            "- **Wallet Connect**: Available in Argentina\n"
        ),
    },
    "MLB": {
        "name":        "Brazil",
        "title":       "MercadoPago API — Brazil (MLB)",
        "contact_url": "https://www.mercadopago.com.br/developers",
        "description": (
            "MercadoPago API specification scoped to Brazil (site_id: MLB).\n\n"
            "## Brazil-specific notes\n\n"
            "- **Currency**: BRL — send amounts as decimals (e.g., `100.50` for R$100,50)\n\n"
            "- **Pix**: Available 24/7, instant settlement — `payment_method_id: pix`\n\n"
            "- **Boleto Bancário**: Cash payment, expires 1–3 days — `payment_method_id: bolbradesco`\n\n"
            "- **Identification**: `CPF` for individuals (11 digits), `CNPJ` for companies (14 digits)\n\n"
            "- **Card brands**: Visa, Mastercard, Elo (local), Hipercard (local), Amex\n\n"
            "- **Point**: Available in Brazil\n\n"
            "- **Wallet Connect**: Available in Brazil\n\n"
            "- **Payouts**: Pix disbursements and bank transfers available\n"
        ),
    },
    "MLM": {
        "name":        "Mexico",
        "title":       "MercadoPago API — Mexico (MLM)",
        "contact_url": "https://www.mercadopago.com.mx/developers",
        "description": (
            "MercadoPago API specification scoped to Mexico (site_id: MLM).\n\n"
            "## Mexico-specific notes\n\n"
            "- **Currency**: MXN — send amounts as decimals (e.g., `500.00`)\n\n"
            "- **OXXO**: Cash payment at OXXO stores — `payment_method_id: oxxo`\n\n"
            "- **Identification**: `RFC` for tax purposes, `CURP` for individuals\n\n"
            "- **Card brands**: Visa, Mastercard, Amex\n\n"
            "- **Point**: Available in Mexico\n"
        ),
    },
    "MLC": {
        "name":        "Chile",
        "title":       "MercadoPago API — Chile (MLC)",
        "contact_url": "https://www.mercadopago.cl/developers",
        "description": (
            "MercadoPago API specification scoped to Chile (site_id: MLC).\n\n"
            "## Chile-specific notes\n\n"
            "- **Currency**: CLP — send amounts as integers (no decimals)\n\n"
            "- **Identification**: `RUT` (Rol Único Tributario)\n\n"
            "- **Card brands**: Visa, Mastercard, Amex, Redcompra (debit)\n\n"
            "- **Payouts**: Bank transfers available\n"
        ),
    },
    "MCO": {
        "name":        "Colombia",
        "title":       "MercadoPago API — Colombia (MCO)",
        "contact_url": "https://www.mercadopago.com.co/developers",
        "description": (
            "MercadoPago API specification scoped to Colombia (site_id: MCO).\n\n"
            "## Colombia-specific notes\n\n"
            "- **Currency**: COP — send amounts as integers (no decimals)\n\n"
            "- **Identification**: `CC` (Cédula de Ciudadanía), `NIT` (companies)\n\n"
            "- **Card brands**: Visa, Mastercard, Amex, Codensa\n\n"
            "- **PSE**: Bank transfer via PSE network\n"
        ),
    },
    "MPE": {
        "name":        "Peru",
        "title":       "MercadoPago API — Peru (MPE)",
        "contact_url": "https://www.mercadopago.com.pe/developers",
        "description": (
            "MercadoPago API specification scoped to Peru (site_id: MPE).\n\n"
            "## Peru-specific notes\n\n"
            "- **Currency**: PEN — send amounts as decimals (e.g., `50.00`)\n\n"
            "- **Identification**: `DNI` (Documento Nacional de Identidad)\n\n"
            "- **Card brands**: Visa, Mastercard, Amex, Diners\n\n"
            "- **PagoEfectivo**: Cash payment network available\n"
        ),
    },
    "MLU": {
        "name":        "Uruguay",
        "title":       "MercadoPago API — Uruguay (MLU)",
        "contact_url": "https://www.mercadopago.com.uy/developers",
        "description": (
            "MercadoPago API specification scoped to Uruguay (site_id: MLU).\n\n"
            "## Uruguay-specific notes\n\n"
            "- **Currency**: UYU — send amounts as decimals (e.g., `500.00`)\n\n"
            "- **Identification**: `CI` (Cédula de Identidad)\n\n"
            "- **Card brands**: Visa, Mastercard, OCA (local), Cabal\n"
        ),
    },
}

# ---------------------------------------------------------------------------
# JSONPath resolver (supports patterns used in overlays)
# ---------------------------------------------------------------------------

def _parse_segments(path: str) -> list[str]:
    """
    Parse a JSONPath string into a list of key segments.
    Handles: $.key, $.key.key, $.paths['/v1/payments'].post.examples
    """
    # Strip leading "$" and optional "."
    path = re.sub(r"^\$\.?", "", path)
    segments: list[str] = []
    i = 0
    while i < len(path):
        if path[i] == "[":
            j = path.index("]", i)
            key = path[i + 1 : j].strip("'\"")
            segments.append(key)
            i = j + 1
            if i < len(path) and path[i] == ".":
                i += 1
        elif path[i] == ".":
            i += 1
        else:
            j = i
            while j < len(path) and path[j] not in ".[]":
                j += 1
            if i < j:
                segments.append(path[i:j])
            i = j
    return segments


def get_at_path(obj: Any, path: str) -> Any:
    """Return the value at JSONPath *path* in *obj*, or None if not found."""
    for seg in _parse_segments(path):
        if not isinstance(obj, dict) or seg not in obj:
            return None
        obj = obj[seg]
    return obj


def set_at_path(obj: dict[str, Any], path: str, value: Any) -> None:
    """Set *value* at JSONPath *path* in *obj*, creating intermediate dicts."""
    segments = _parse_segments(path)
    if not segments:
        return
    for seg in segments[:-1]:
        if seg not in obj or not isinstance(obj[seg], dict):
            obj[seg] = {}
        obj = obj[seg]
    obj[segments[-1]] = value


def merge_at_path(obj: dict[str, Any], path: str, update: Any) -> None:
    """
    Deep-merge *update* into the value at *path*.
    - If the target is a dict and update is a dict → merge keys.
    - Otherwise → replace.
    """
    current = get_at_path(obj, path)
    if isinstance(current, dict) and isinstance(update, dict):
        merged = deep_merge(current, update)
        set_at_path(obj, path, merged)
    else:
        set_at_path(obj, path, update)


def deep_merge(base: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge *update* into *base*. Returns a new dict."""
    result = dict(base)
    for k, v in update.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = deep_merge(result[k], v)
        else:
            result[k] = v
    return result


# ---------------------------------------------------------------------------
# Overlay application (OAL 1.0)
# ---------------------------------------------------------------------------

def apply_overlay(spec: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """
    Apply an OAL 1.0 overlay to *spec*.

    Each action has:
        target:  JSONPath expression
        update:  value to merge at that path
        remove:  (optional) boolean — remove the target instead of updating
    """
    result = copy.deepcopy(spec)
    actions: list[dict[str, Any]] = overlay.get("actions", [])

    for action in actions:
        target = action.get("target", "")
        if not target:
            continue

        if action.get("remove", False):
            # Remove the key at target
            segments = _parse_segments(target)
            if segments:
                parent = get_at_path(result, "$." + ".".join(segments[:-1])) if len(segments) > 1 else result
                if isinstance(parent, dict):
                    parent.pop(segments[-1], None)
        elif "update" in action:
            merge_at_path(result, target, action["update"])

    return result


# ---------------------------------------------------------------------------
# Country-specific info injection
# ---------------------------------------------------------------------------

def inject_country_info(spec: dict[str, Any], site: str) -> dict[str, Any]:
    """Update info.title, info.description, and info.contact for *site*."""
    meta = COUNTRY_META.get(site)
    if not meta:
        return spec

    result = copy.deepcopy(spec)
    result.setdefault("info", {})
    result["info"]["title"]       = meta["title"]
    result["info"]["description"] = meta["description"]
    result["info"].setdefault("contact", {})
    result["info"]["contact"]["name"] = "MercadoPago Developer Experience"
    result["info"]["contact"]["url"]  = meta["contact_url"]
    return result


# ---------------------------------------------------------------------------
# Main bundle logic
# ---------------------------------------------------------------------------

def load_yaml(path: Path) -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


def save_yaml(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        yaml.dump(
            data, f,
            allow_unicode=True,
            sort_keys=False,
            default_flow_style=False,
            width=120,
        )


# ---------------------------------------------------------------------------
# Phase 1: Schema bundling — schemas/*.yaml → spec3.yaml components/schemas
# ---------------------------------------------------------------------------

def bundle_schemas(spec: dict[str, Any], dry_run: bool = False) -> tuple[dict[str, Any], list[str]]:
    """
    Read all schemas/*.yaml fragments and merge their components/schemas
    into *spec*. Returns (updated_spec, changes).

    Each schema file must have structure:
        components:
          schemas:
            SchemaName:
              ...
    """
    spec = copy.deepcopy(spec)
    spec.setdefault("components", {}).setdefault("schemas", {})
    changes: list[str] = []

    schema_files = sorted(SCHEMAS.glob("*.yaml"))
    if not schema_files:
        print("  No schema fragments found in schemas/")
        return spec, changes

    for schema_file in schema_files:
        fragment = load_yaml(schema_file)
        new_schemas: dict[str, Any] = (
            fragment.get("components", {}).get("schemas", {})
        )
        if not new_schemas:
            print(f"  [{schema_file.name}] No components/schemas found — skipping")
            continue

        for name, schema in new_schemas.items():
            existing = spec["components"]["schemas"].get(name)
            if existing is None:
                spec["components"]["schemas"][name] = schema
                changes.append(f"+ schema: {name}  ({schema_file.name})")
                print(f"  + {name}  ← {schema_file.name}")
            elif existing != schema:
                spec["components"]["schemas"][name] = schema
                changes.append(f"~ schema: {name}  ({schema_file.name})")
                print(f"  ~ {name}  ← {schema_file.name}")

    return spec, changes


def bundle_site(site: str, base_spec: dict[str, Any], dry_run: bool = False) -> bool:
    """
    Apply overlay for *site* to *base_spec* and write by-site/{site}/spec3.yaml.
    Returns True if the output changed.
    """
    overlay_path = OVERLAYS / f"{site}.yaml"
    out_path     = BY_SITE / site / "spec3.yaml"

    if not overlay_path.exists():
        print(f"  [{site}] No overlay found at {overlay_path} — skipping")
        return False

    overlay = load_yaml(overlay_path)
    result  = apply_overlay(base_spec, overlay)
    result  = inject_country_info(result, site)

    # Check if output changed
    existing_yaml = out_path.read_text() if out_path.exists() else ""
    new_yaml = yaml.dump(
        result, allow_unicode=True, sort_keys=False,
        default_flow_style=False, width=120,
    )

    if new_yaml == existing_yaml:
        print(f"  [{site}] No changes")
        return False

    if dry_run:
        print(f"  [{site}] Would update {out_path}")
        return True

    save_yaml(out_path, result)
    print(f"  [{site}] Written → {out_path}")
    return True


def check_mode(sites: list[str]) -> int:
    """
    CI check: return exit code 1 if any by-site spec is out of date.
    """
    base_spec = load_yaml(SPEC3_PATH)
    stale: list[str] = []

    for site in sites:
        overlay_path = OVERLAYS / f"{site}.yaml"
        out_path     = BY_SITE / site / "spec3.yaml"
        if not overlay_path.exists():
            continue

        overlay  = load_yaml(overlay_path)
        result   = apply_overlay(base_spec, overlay)
        result   = inject_country_info(result, site)
        new_yaml = yaml.dump(result, allow_unicode=True, sort_keys=False,
                             default_flow_style=False, width=120)
        existing = out_path.read_text() if out_path.exists() else ""

        if new_yaml != existing:
            stale.append(site)
            print(f"  STALE: {site}")
        else:
            print(f"  OK:    {site}")

    if stale:
        print(f"\nRun `python scripts/bundle.py` to regenerate: {', '.join(stale)}")
        return 1
    print("\nAll by-site specs are up to date.")
    return 0


# ---------------------------------------------------------------------------
# Phase 3: By-product generation — filter spec3.yaml per product
# ---------------------------------------------------------------------------

def _collect_referenced_schemas(obj: Any, all_schemas: dict[str, Any], collected: set[str] | None = None) -> set[str]:
    """BFS over obj collecting all #/components/schemas/{name} $refs recursively."""
    if collected is None:
        collected = set()
    queue = [obj]
    while queue:
        current = queue.pop()
        if not current or not isinstance(current, (dict, list)):
            continue
        if isinstance(current, list):
            queue.extend(current)
            continue
        ref = current.get("$ref", "")
        if isinstance(ref, str) and ref.startswith("#/components/schemas/"):
            name = ref.rsplit("/", 1)[-1]
            if name not in collected:
                collected.add(name)
                if name in all_schemas:
                    queue.append(all_schemas[name])
            continue
        queue.extend(current.values())
    return collected


def filter_spec_by_paths(spec: dict[str, Any], allowed_paths: list[str]) -> dict[str, Any]:
    """Return a copy of spec with only paths matching allowed_paths and their schemas."""
    result = copy.deepcopy(spec)

    filtered_paths = {
        path: methods
        for path, methods in result.get("paths", {}).items()
        if any(path.startswith(prefix) for prefix in allowed_paths)
    }
    result["paths"] = filtered_paths

    all_schemas = result.get("components", {}).get("schemas", {})
    referenced = _collect_referenced_schemas(filtered_paths, all_schemas)

    if "components" in result and "schemas" in result["components"]:
        result["components"]["schemas"] = {
            name: schema
            for name, schema in all_schemas.items()
            if name in referenced
        }

    return result


def bundle_product(app_config: dict[str, Any], spec: dict[str, Any], dry_run: bool = False) -> bool:
    """
    Filter spec to paths owned by app_config and write by-product/{product}/spec3.yaml.
    Returns True if the output changed.
    """
    product = app_config.get("product") or app_config.get("fury_app")
    allowed_paths = app_config.get("paths", [])
    out_path = BY_PRODUCT / product / "spec3.yaml"

    if not allowed_paths:
        print(f"  [{product}] No paths defined — skipping")
        return False

    filtered = filter_spec_by_paths(spec, allowed_paths)

    existing_yaml = out_path.read_text() if out_path.exists() else ""
    new_yaml = yaml.dump(filtered, allow_unicode=True, sort_keys=False,
                         default_flow_style=False, width=120)

    if new_yaml == existing_yaml:
        print(f"  [{product}] No changes")
        return False

    if dry_run:
        print(f"  [{product}] Would update {out_path}")
        return True

    save_yaml(out_path, filtered)
    print(f"  [{product}] Written → {out_path}")
    return True


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    all_sites = sorted(s.stem for s in OVERLAYS.glob("*.yaml"))

    parser = argparse.ArgumentParser(
        description=(
            "Phase 1: merge schemas/*.yaml → spec3.yaml  |  "
            "Phase 2: apply overlays → by-site/{SITE}/spec3.yaml  |  "
            "Phase 3: filter per product → by-product/{product}/spec3.yaml"
        )
    )
    parser.add_argument(
        "sites", nargs="*", metavar="SITE",
        help=f"Sites for phase 2 (default: all). Available: {', '.join(all_sites)}",
    )
    parser.add_argument("--schemas-only", action="store_true",
                        help="Run only phase 1 (schema bundling)")
    parser.add_argument("--sites-only", action="store_true",
                        help="Run only phase 2 (by-site generation)")
    parser.add_argument("--products-only", action="store_true",
                        help="Run only phase 3 (by-product generation)")
    parser.add_argument("--check", action="store_true",
                        help="CI mode: exit 1 if spec3.yaml or any by-site is stale")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would change without writing files")
    args = parser.parse_args()

    sites = args.sites if args.sites else all_sites

    # ── CI check mode ────────────────────────────────────────────────────────
    if args.check:
        print("=== Phase 1: schema check ===")
        spec = load_yaml(SPEC3_PATH)
        _, schema_changes = bundle_schemas(spec, dry_run=True)
        if schema_changes:
            print(f"\nspec3.yaml has {len(schema_changes)} stale schema(s).")
            print("Run `python scripts/bundle.py --schemas-only` to fix.")
            sys.exit(1)
        print("  schemas OK\n")

        print("=== Phase 2: by-site check ===")
        sys.exit(check_mode(sites))

    only_one = args.schemas_only or args.sites_only or args.products_only

    # ── Phase 1: schema bundle ────────────────────────────────────────────────
    if not args.sites_only and not args.products_only:
        print("=== Phase 1: bundling schemas/*.yaml → spec3.yaml ===\n")
        spec = load_yaml(SPEC3_PATH)
        updated_spec, schema_changes = bundle_schemas(spec, dry_run=args.dry_run)

        if schema_changes and not args.dry_run:
            save_yaml(SPEC3_PATH, updated_spec)
            print(f"\nspec3.yaml updated ({len(schema_changes)} schema change(s)).")
        elif not schema_changes:
            print("  No schema changes.")
        else:
            print(f"\nDry run — {len(schema_changes)} schema change(s) would be applied.")

    # ── Phase 2: by-site generation ───────────────────────────────────────────
    if not args.schemas_only and not args.products_only:
        print(f"\n=== Phase 2: generating by-site for {', '.join(sites)} ===\n")
        base_spec = load_yaml(SPEC3_PATH)
        changed = 0

        for site in sites:
            if site not in COUNTRY_META and site not in all_sites:
                print(f"  [{site}] Unknown site — skipping")
                continue
            if bundle_site(site, base_spec, dry_run=args.dry_run):
                changed += 1

        print(f"\nDone. {changed}/{len(sites)} site(s) updated.")

    # ── Phase 3: by-product generation ───────────────────────────────────────
    if not args.schemas_only and not args.sites_only:
        if not APPS_CONFIG.exists():
            print("\n=== Phase 3: skipped — apps.yaml not found ===")
        else:
            apps_config = load_yaml(APPS_CONFIG)
            apps = apps_config.get("apps", [])
            print(f"\n=== Phase 3: generating by-product for {len(apps)} app(s) ===\n")
            base_spec = load_yaml(SPEC3_PATH)
            changed = 0

            for app_config in apps:
                if bundle_product(app_config, base_spec, dry_run=args.dry_run):
                    changed += 1

            print(f"\nDone. {changed}/{len(apps)} product(s) updated.")


if __name__ == "__main__":
    main()
