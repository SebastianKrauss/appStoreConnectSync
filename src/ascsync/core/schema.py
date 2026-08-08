"""Check the resource declarations against Apple's own OpenAPI specification.

Four of the six bugs the first real push exposed were a wrong field or
relationship name. Every one of them was sitting in plain sight in a document
Apple publishes: the App Store Connect OpenAPI specification.

So compare the two. For every resource declared under `resources/`, look up its
create and update request in the spec and report:

  - fields we would send that the API does not accept  → a bug, waiting
  - fields the API accepts that we never send          → maybe an omission

This does not generate the declarations, and deliberately so. The declarations
carry decisions the spec cannot: which fields matter, which are required for
*submission* rather than for the API, what a sensible limit is, and why a field
was left out. Generated code would lose all of that. Checking keeps the
judgement and catches the typos.

    ascsync schema check                 # downloads the spec, ~7 MB, cached
    ascsync schema check --spec path.json

The spec is not vendored: it changes on Apple's schedule, and a copy in the
repository would quietly go stale — which is exactly the failure this is meant
to prevent.
"""
from __future__ import annotations

import io
import json
import os
import zipfile
from typing import Dict, List, Optional, Set, Tuple

SPEC_URL = ("https://developer.apple.com/sample-code/app-store-connect/"
            "app-store-connect-openapi-specification.zip")
CACHE = os.path.join(os.path.expanduser("~"), ".cache", "ascsync", "openapi.json")


def load_spec(path: Optional[str] = None, refresh: bool = False) -> dict:
    """Apple's spec: from a file, from the cache, or freshly downloaded."""
    if path:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    if not refresh and os.path.exists(CACHE):
        with open(CACHE, encoding="utf-8") as f:
            return json.load(f)
    try:
        import requests
    except ImportError:                                # pragma: no cover
        raise SystemExit("requests is missing — pip install -e .")
    print(f"Downloading {SPEC_URL} …")
    response = requests.get(SPEC_URL, timeout=120)
    response.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
        name = next(n for n in archive.namelist()
                    if n.endswith(".json") and not n.startswith("__MACOSX"))
        spec = json.loads(archive.read(name).decode("utf-8"))
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    with open(CACHE, "w", encoding="utf-8") as f:
        json.dump(spec, f)
    return spec


def _candidates(resource_type: str) -> List[str]:
    """Schema names Apple might have used for this resource type.

    `inAppPurchases` lives under `InAppPurchaseV2CreateRequest`, several Game
    Center resources exist in both a plain and a V2 flavour. Try them all and
    take whatever is there.
    """
    base = resource_type[:-1] if resource_type.endswith("s") else resource_type
    cap = base[0].upper() + base[1:]
    return [f"{cap}CreateRequest", f"{cap}UpdateRequest",
            f"{cap}V2CreateRequest", f"{cap}V2UpdateRequest"]


def _attributes(schemas: dict, name: str) -> Set[str]:
    data = schemas.get(name, {}).get("properties", {}).get("data", {})
    return set((data.get("properties", {}).get("attributes", {})
                .get("properties") or {}).keys())


def _relationships(schemas: dict, name: str) -> Set[str]:
    data = schemas.get(name, {}).get("properties", {}).get("data", {})
    return set((data.get("properties", {}).get("relationships", {})
                .get("properties") or {}).keys())


def known_fields(spec: dict, resource_type: str) -> Tuple[Set[str], Set[str], List[str]]:
    """(attributes, relationships, which schemas were consulted)."""
    schemas = spec.get("components", {}).get("schemas", {})
    used = [name for name in _candidates(resource_type) if name in schemas]
    attributes: Set[str] = set()
    relationships: Set[str] = set()
    for name in used:
        attributes |= _attributes(schemas, name)
        relationships |= _relationships(schemas, name)
    return attributes, relationships, used


def _walk(resource, seen: Set[str]):
    if resource.type in seen:
        return
    seen.add(resource.type)
    yield resource
    for child in resource.children:
        yield from _walk(child, seen)


def check(spec: dict) -> Tuple[List[str], List[str], List[str]]:
    """(problems, suggestions, notes) — problems are the ones that break a push."""
    from ..resources import ALL_DOMAINS
    problems: List[str] = []
    suggestions: List[str] = []
    notes: List[str] = []
    seen: Set[str] = set()

    for domain in ALL_DOMAINS:
        for resource in _walk(domain.resource, seen):
            attributes, relationships, used = known_fields(spec, resource.type)
            if not used:
                notes.append(f"{resource.type}: not in the specification — "
                             f"cannot be checked")
                continue
            declared = set(resource.writable)
            unknown = sorted(declared - attributes)
            for field in unknown:
                problems.append(f"{resource.type}.{field}: we would send it, the "
                                f"API does not accept it "
                                f"(checked against {', '.join(used)})")
            # The natural key is carried as the record's key, not as a field
            # somebody forgot to declare.
            missing = sorted(attributes - declared - set(resource.readonly)
                             - {resource.key})
            if missing:
                suggestions.append(f"{resource.type}: not declared — "
                                   f"{', '.join(missing)}")
            if resource.parent_rel and relationships and \
                    resource.parent_rel not in relationships:
                problems.append(f"{resource.type}: parent relationship "
                                f"'{resource.parent_rel}' is unknown; the spec "
                                f"has {', '.join(sorted(relationships))}")
    return problems, suggestions, notes


def report(spec: dict) -> Tuple[int, List[str]]:
    problems, suggestions, notes = check(spec)
    version = spec.get("info", {}).get("version", "?")
    lines = [f"Checked against App Store Connect API {version}.", ""]
    if problems:
        lines.append("Would break a push:")
        lines += [f"  - {p}" for p in problems]
        lines.append("")
    if suggestions:
        lines.append("Offered by the API, not declared here "
                     "(often deliberate — prices, state, ids):")
        lines += [f"  . {s}" for s in suggestions]
        lines.append("")
    if notes:
        lines.append("Not in the specification:")
        lines += [f"  . {n}" for n in notes]
        lines.append("")
    lines.append(f"{len(problems)} problem(s), {len(suggestions)} suggestion(s).")
    return (1 if problems else 0), lines
