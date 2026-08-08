"""Offline checks — no API access, no credentials needed.

They cover what Apple would otherwise only reject at upload time: character
limits, required fields, language completeness, asset files (resolution,
aspect ratio, alpha, format), event deadlines and quotas, and the ids against
your app's source.
"""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Dict, List, Optional

from . import assets as assetlib
from . import domains, paths
from .registry import Domain, Resource

def _code_config() -> dict:
    """The optional "code" section of data/app.json.

    Without it the source cross-check is simply skipped — the tool works fine
    for projects whose code lives elsewhere, or is not written in Swift.

        "code": {
          "sourceDir": "../MyApp",           # relative to the project root
          "labelsDir": "Resources/labels",   # relative to sourceDir, for .lproj
          "sourceSuffix": ".swift"
        }
    """
    return (paths.load_app_config().get("code") or {})


def source_dir() -> Optional[str]:
    configured = _code_config().get("sourceDir")
    if not configured:
        return None
    path = os.path.join(paths.PROJECT_ROOT, os.path.expanduser(configured))
    return path if os.path.isdir(path) else None


def labels_dir() -> Optional[str]:
    base = source_dir()
    relative = _code_config().get("labelsDir")
    if not base or not relative:
        return None
    path = os.path.join(base, relative)
    return path if os.path.isdir(path) else None


def id_literal_pattern() -> Optional[re.Pattern]:
    """Which string literals in the source are ids? -> the app's idPrefix."""
    prefix = (paths.load_app_config().get("idPrefix") or "").strip()
    if not prefix:
        return None
    return re.compile(r'"(' + re.escape(prefix.rstrip(".")) + r'[^"]*)"')


# ---------------------------------------------------------------------------
def validate_domain(domain: Domain, locales: List[str],
                    check_assets: bool = True,
                    readiness: bool = False) -> List[str]:
    problems: List[str] = []
    doc = domains.load_doc(domain)
    if doc is None:
        return [f"{domain.name}: data/{domain.data_file} fehlt "
                f"(create it, or run 'ascsync pull --domain {domain.group or domain.name}')"]
    items = domains.doc_items(doc, domain.resource)
    for item in items:
        key = str(item.get(domain.resource.key, "?"))
        problems.extend(_check_item(domain.resource, item, key, locales,
                                    check_assets, {"key": key}, readiness))
    return problems


def _check_item(resource: Resource, item: dict, path: str, locales: List[str],
                check_assets: bool, fmt: dict,
                readiness: bool = False) -> List[str]:
    problems: List[str] = []
    if readiness:
        for name, spec in resource.writable.items():
            if not spec.submission:
                continue
            value = item.get(name)
            if value is None or (isinstance(value, str) and not value.strip()):
                problems.append(f"{path}/{name}: empty — required for submission")
    for name, spec in resource.writable.items():
        value = item.get(name)
        if spec.required and (value is None or value == ""):
            problems.append(f"{path}/{name}: Pflichtfeld fehlt")
        if isinstance(value, str) and spec.limit and len(value) > spec.limit:
            problems.append(f"{path}/{name}: {len(value)} characters (limit {spec.limit})")
        if spec.choices and value and value not in spec.choices:
            problems.append(f"{path}/{name}: '{value}' unbekannt "
                            f"(erlaubt: {', '.join(spec.choices)})")

    # Anything the push would skip as 'incomplete' shows up here already.
    from .registry import RequireTogether
    for quirk in resource.quirk(RequireTogether):
        empty = [f for f in quirk.fields if not str(item.get(f) or "").strip()]
        if empty and len(empty) < len(quirk.fields):
            problems.append(f"{path}: {', '.join(empty)} empty — the push would skip "
                            f"the whole record")
        elif empty:
            problems.append(f"{path}: Texte fehlen ({', '.join(quirk.fields)})")

    item_fmt = dict(fmt)
    item_fmt.update({k: v for k, v in item.items() if isinstance(v, str)})
    item_fmt.setdefault("slug", "")
    item_fmt["key"] = str(item.get(resource.key, ""))
    item_fmt["slug"] = _slug(item_fmt["key"])

    for child in resource.children:
        from .engine import collection_items
        children = collection_items(item, child)
        if child.key == "locale":
            have = {str(c.get("locale")) for c in children}
            for locale in locales:
                if locale not in have:
                    problems.append(f"{path}/{child.doc_key()}: {locale} fehlt")
        if readiness and child.singleton and not children:
            problems.append(f"{path}/{child.doc_key()}: block missing — "
                            f"needed for submission ({child.type})")
        child_fmt = dict(item_fmt)
        child_fmt["parent_key"] = item_fmt["key"]
        child_fmt["parent_slug"] = item_fmt["slug"]
        for entry in children:
            label = (f"{path}/{child.doc_key()}" if child.singleton
                     else f"{path}/{child.doc_key()}/{entry.get(child.key)}")
            problems.extend(_check_item(child, entry, label, locales,
                                        check_assets, child_fmt, readiness))

    if check_assets:
        for spec in resource.assets:
            problems.extend(_check_asset(spec, item_fmt, path))
    return problems


def _check_asset(spec, fmt: dict, path: str) -> List[str]:
    if spec.single:
        local = assetlib.resolve_asset(spec.path, spec.fallbacks, **fmt)
        if not local:
            try:
                expected = spec.path.format(**fmt)
            except KeyError:
                return []
            return [f"{path}/{spec.name}: file missing (assets/{expected})"]
        return [f"{path}/{spec.name}: {p}" for p in assetlib.check_file(local, spec.rule)]
    # Ordered sets: one directory per display type
    try:
        base = paths.asset_path(spec.path.format(**fmt))
    except KeyError:
        return []
    if not os.path.isdir(base):
        return []
    problems = []
    for display_type in sorted(os.listdir(base)):
        directory = os.path.join(base, display_type)
        if not os.path.isdir(directory) or display_type.startswith("."):
            continue
        files = assetlib.ordered_files(
            directory, tuple(spec.rule.formats if spec.rule else (".png",))
            + assetlib.VIDEO_EXTENSIONS)
        if len(files) > 10:
            problems.append(f"{path}/{spec.name}/{display_type}: {len(files)} files "
                            f"(Apple allows at most 10)")
        for local in files:
            problems.extend(f"{path}/{spec.name}/{display_type}: {p}"
                            for p in assetlib.check_file(local, spec.rule))
        problems.extend(_check_display_type(spec, display_type, files, path))
    return problems


def _check_display_type(spec, display_type: str, files, path: str) -> List[str]:
    """Is the display type known, and do the dimensions match it?"""
    from ..resources.app_store import SCREENSHOT_SIZES
    if spec.name != "screenshots" or display_type not in SCREENSHOT_SIZES:
        if spec.name == "screenshots":
            return [f"{path}/{spec.name}/{display_type}: unknown display type — "
                    f"known: {', '.join(sorted(SCREENSHOT_SIZES))}"]
        return []
    allowed = SCREENSHOT_SIZES[display_type]
    problems = []
    for local in files:
        size = assetlib.image_size(local)
        if size and (size[0], size[1]) not in allowed:
            erlaubt = ", ".join(f"{w}x{h}" for w, h in allowed)
            problems.append(f"{path}/{spec.name}/{display_type}/"
                            f"{os.path.basename(local)}: {size[0]}x{size[1]} — "
                            f"erlaubt: {erlaubt}")
    return problems


def _slug(key: str) -> str:
    prefix = paths.load_app_config().get("idPrefix") or ""
    return key[len(prefix):] if prefix and key.startswith(prefix) else key


# ---------------------------------------------------------------------------
# App languages vs. store languages
# ---------------------------------------------------------------------------
def validate_app_languages(locales: List[str]) -> List[str]:
    directory = labels_dir()
    if not directory:
        return []
    available = {name[:-6] for name in os.listdir(directory) if name.endswith(".lproj")}
    problems = []
    for locale in locales:
        language = locale.split("-")[0]
        if language not in available and locale not in available:
            problems.append(
                f"locales.json: {locale} — the app has no "
                f"{language}.lproj in {paths.rel_to_asc(directory)}. A store "
                f"page in a language the app does not speak is a mistake.")
    return problems


# ---------------------------------------------------------------------------
# Ids im Swift-Code vs. data/
# ---------------------------------------------------------------------------
def swift_identifiers() -> Dict[str, List[str]]:
    """Every id-shaped string literal in the app's source.

    Interpolierte Literale (z. B. "…shape.type.\\(shapeType).\\($0)") kommen als
    pattern, so generated families are not falsely reported as missing.

    Without a configured "code" section in data/app.json this returns empty
    lists and the cross-check is silently skipped.
    """
    literals: List[str] = []
    patterns: List[str] = []
    directory = source_dir()
    id_literal = id_literal_pattern()
    if not directory or not id_literal:
        return {"literals": [], "patterns": []}
    suffix = _code_config().get("sourceSuffix") or ".swift"
    for root, _dirs, files in os.walk(directory):
        for name in files:
            if not name.endswith(suffix):
                continue
            with open(os.path.join(root, name), encoding="utf-8", errors="ignore") as f:
                for match in id_literal.findall(f.read()):
                    if "\\(" in match:
                        patterns.append(interpolation_pattern(match))
                    else:
                        literals.append(match)
    return {"literals": sorted(set(literals)), "patterns": sorted(set(patterns))}


def interpolation_pattern(literal: str) -> str:
    """A Swift string with interpolation -> a regex.

    "…app.duration.\\(Int($0))" -> ^…app\\.duration\\..+$ — the parentheses
    have to be counted, otherwise a nested call such as \\(Int($0)) leaves a
    stray closing bracket in the pattern.
    """
    out = []
    index = 0
    while index < len(literal):
        start = literal.find("\\(", index)
        if start < 0:
            out.append(re.escape(literal[index:]))
            break
        out.append(re.escape(literal[index:start]))
        depth = 0
        position = start + 1                 # sits on the '('
        while position < len(literal):
            if literal[position] == "(":
                depth += 1
            elif literal[position] == ")":
                depth -= 1
                if depth == 0:
                    break
            position += 1
        out.append(".+")
        index = position + 1
    return "^" + "".join(out) + "$"


def validate_code_drift(domain_ids: Dict[str, List[str]]) -> List[str]:
    """domain_ids: {'achievements': [...], 'iap': [...], 'leaderboards': [...]}"""
    swift = swift_identifiers()
    literals = set(swift["literals"])
    patterns = [re.compile(p) for p in swift["patterns"]]
    known = {i for ids in domain_ids.values() for i in ids}
    problems = []

    for identifier in sorted(literals - known):
        problems.append(f"the source knows '{identifier}', data/ does not — missing in ASC?")
    for name, ids in domain_ids.items():
        for identifier in ids:
            if identifier in literals:
                continue
            if any(p.match(identifier) for p in patterns):
                continue
            problems.append(f"{name}: '{identifier}' is in data/ but in no source "
                            f"file — orphaned?")
    return problems


# ---------------------------------------------------------------------------
# Events: deadlines and quotas
# ---------------------------------------------------------------------------
def validate_events(items: List[dict]) -> List[str]:
    from ..resources import events as events_res
    problems: List[str] = []
    published = 0
    for item in items:
        ref = item.get("referenceName", "?")
        for schedule in item.get("territorySchedules") or []:
            start = _parse(schedule.get("eventStart"))
            end = _parse(schedule.get("eventEnd"))
            publish = _parse(schedule.get("publishStart"))
            if start and end:
                days = (end - start).days
                if days > events_res.MAX_DURATION_DAYS:
                    problems.append(f"{ref}: duration {days} days (Apple allows at "
                                    f"most {events_res.MAX_DURATION_DAYS})")
                if end <= start:
                    problems.append(f"{ref}: end is not after the start")
            if publish and start:
                lead = (start - publish).days
                if lead > events_res.MAX_PUBLISH_LEAD_DAYS:
                    problems.append(f"{ref}: published {lead} days before the start "
                                    f"(Apple allows at most "
                                    f"{events_res.MAX_PUBLISH_LEAD_DAYS})")
                if publish > start:
                    problems.append(f"{ref}: publication is after the start")
            published += 1
    if len(items) > events_res.MAX_APPROVED:
        problems.append(f"{len(items)} events in data/ — ASC accepts at most "
                        f"{events_res.MAX_APPROVED} approved at a time")
    return problems


# ---------------------------------------------------------------------------
# Submission readiness: what the API does not demand but submission does
# ---------------------------------------------------------------------------
# Not checkable offline — belongs in the report so it is not forgotten.
NOT_CHECKABLE = (
    "prices per IAP and the app price (only maintainable in ASC)",
    "export compliance answer (once, in ASC)",
    "build uploaded and attached to the version",
    "release records per achievement/leaderboard "
    "('ascsync releases --yes', needs the live API)",
)


def validate_readiness(locales: List[str]) -> List[str]:
    """Structural checks that do not hang off any single field."""
    problems: List[str] = []
    problems.extend(_check_categories())
    problems.extend(_check_screenshots(locales))
    problems.extend(_check_privacy())
    problems.extend(_check_snapshot())
    problems.extend(_check_event_recurrence())
    return problems


def _check_categories() -> List[str]:
    categories = paths.load_app_config().get("categories") or {}
    if not str(categories.get("primaryCategory") or "").strip():
        return ["app.json/categories.primaryCategory: empty — without a category "
                "ASC will not accept the submission"]
    return []


def _check_screenshots(locales: List[str]) -> List[str]:
    from ..resources.app_store import REQUIRED_DISPLAY_TYPES
    problems = []
    for locale in locales:
        for display_type in REQUIRED_DISPLAY_TYPES:
            directory = paths.asset_path("screenshots", locale, display_type)
            count = len(assetlib.ordered_files(directory, (".png", ".jpg", ".jpeg")))
            if count == 0:
                problems.append(f"screenshots/{locale}/{display_type}: no file — "
                                f"Apple requires at least one per display type")
    return problems


def _check_privacy() -> List[str]:
    from ..resources.privacy import PRIVACY_DOMAIN, USAGES
    doc = domains.load_doc(PRIVACY_DOMAIN)
    entries = [i for i in domains.doc_items(doc, USAGES)
               if str(i.get("key")) != "publishState"] if doc else []
    if not entries:
        return ["privacy.json: no data usage recorded — without app privacy "
                "Apple will not accept the submission. 'No data collected' is "
                "a valid answer, but it has to be stated explicitly."]
    return []


def _check_snapshot() -> List[str]:
    if not os.path.isdir(paths.SNAPSHOT_DIR):
        return ["'.snapshot/': missing — run 'ascsync pull' before the first push"]
    files = [n for n in os.listdir(paths.SNAPSHOT_DIR) if n.endswith(".json")]
    subdirs = [n for n in os.listdir(paths.SNAPSHOT_DIR)
               if os.path.isdir(os.path.join(paths.SNAPSHOT_DIR, n))]
    if not files and not subdirs:
        return ["'.snapshot/': empty — 'ascsync pull' has never succeeded; without "
                "a snapshot the diff cannot spot changes made by others"]
    return []


def _check_event_recurrence() -> List[str]:
    """Without recurrence dates the event generator has nothing to work from."""
    from ..resources.game_center import LEADERBOARDS, LEADERBOARDS_DOMAIN
    doc = domains.load_doc(LEADERBOARDS_DOMAIN)
    if not doc:
        return []
    templates_path = paths.data_path("events", "templates.json")
    if not os.path.exists(templates_path):
        return []
    wanted = set((paths.read_json(templates_path).get("leaderboards") or {}).values())
    problems = []
    for item in domains.doc_items(doc, LEADERBOARDS):
        key = str(item.get("vendorIdentifier"))
        if key in wanted and not str(item.get("recurrenceStartDate") or "").strip():
            problems.append(f"leaderboards/{key}: recurrenceStartDate empty — "
                            f"without a date 'events generate' produces nothing")
    return problems


def _parse(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = value.replace("Z", "+00:00")
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    return None
