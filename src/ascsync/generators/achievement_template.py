"""Build an achievement scaffold from a declared id scheme.

Game Center achievements almost always come in families: `gift.1`, `gift.3`,
`gift.7`… or `solo.win.10` and `vs.win.10`. Typing those out is dull and the
kind of dull that produces typos, which then show up as a mismatch between your
source and ASC weeks later.

So declare the families once, in `data/gamecenter/achievement_scheme.json`:

    {
      "families": [
        { "suffix": "tutorial.completed", "points": 1 },
        { "suffix": "gift.{n}",
          "values": { "n": [1, 3, 7, 14, 30] },
          "points": { "by": "n", "map": { "1": 1, "7": 5, "30": 10 },
                      "default": 5 } },
        { "suffix": "{mode}.win.{n}",
          "values": { "mode": ["solo", "vs"], "n": [10, 100] } }
      ]
    }

Each family renders the cartesian product of its `values` into `suffix`, and
every id is prefixed with `idPrefix` from `data/app.json`. `points` is either a
number or a lookup keyed by one placeholder. `exclude` drops individual
rendered suffixes — useful when one combination does not exist.

The generator does NOT read your source. It reproduces the scheme you declared,
and `ascsync validate` then tells you whether scheme and source still agree.
That separation is the point: two independent statements of the same truth,
compared by a third party.

Without a scheme file the command does nothing and says so. Maintaining
`achievements.json` by hand is a perfectly reasonable choice for a short list.

  ascsync achievements template            # add missing ids, keep the texts
  ascsync achievements template --force    # regenerate all (texts are lost!)
"""
from __future__ import annotations

import itertools
import os
import re
from typing import Any, Dict, List, Optional

from ..core import domains, paths
from ..resources.game_center import ACHIEVEMENTS, ACHIEVEMENTS_DOMAIN

SCHEME_FILE = "gamecenter/achievement_scheme.json"
GAME_CENTER_LIMIT = 100
DEFAULT_POINTS = 5

_PLACEHOLDER = re.compile(r"\{(\w+)\}")


def scheme_path() -> str:
    return paths.data_path(SCHEME_FILE)


def load_scheme() -> Optional[dict]:
    path = scheme_path()
    return paths.read_json(path) if os.path.exists(path) else None


def prefix() -> str:
    return (paths.load_app_config().get("idPrefix") or "").rstrip(".")


def reference_name(vendor_id: str, root: str) -> str:
    """Internal name shown in ASC — derived, but overridable per achievement.

    An existing referenceName always wins in merge(), so renaming one in
    data/ sticks.
    """
    tail = vendor_id[len(root) + 1:] if root and vendor_id.startswith(root) else vendor_id
    return tail.replace(".", " ").title()


def points_for(family: dict, values: Dict[str, Any]) -> int:
    """A number, or a lookup keyed by one of the family's placeholders."""
    spec = family.get("points", DEFAULT_POINTS)
    if isinstance(spec, (int, float)):
        return int(spec)
    if isinstance(spec, dict):
        by = spec.get("by")
        key = str(values.get(by, ""))
        table = {str(k): v for k, v in (spec.get("map") or {}).items()}
        return int(table.get(key, spec.get("default", DEFAULT_POINTS)))
    return DEFAULT_POINTS


def expand(family: dict) -> List[Dict[str, Any]]:
    """One family -> the list of its placeholder combinations.

    Order follows the declaration, so the generated file stays stable and
    diffs stay readable.
    """
    suffix = str(family.get("suffix") or "")
    names = _PLACEHOLDER.findall(suffix)
    if not names:
        return [{}]
    values = family.get("values") or {}
    missing = [n for n in names if n not in values]
    if missing:
        raise SystemExit(f"achievement_scheme.json: family '{suffix}' has no "
                         f"values for {', '.join(missing)}.")
    lists = [[(n, v) for v in values[n]] for n in names]
    return [dict(combo) for combo in itertools.product(*lists)]


def build(locales: List[str], scheme: dict) -> List[dict]:
    root = prefix()
    out: List[dict] = []
    seen = set()
    for family in scheme.get("families") or []:
        suffix = str(family.get("suffix") or "")
        if not suffix:
            continue
        excluded = {str(x) for x in (family.get("exclude") or [])}
        for values in expand(family):
            tail = suffix.format(**values)
            if tail in excluded:
                continue
            vendor_id = f"{root}.{tail}" if root else tail
            if vendor_id in seen:
                continue
            seen.add(vendor_id)
            name_template = family.get("referenceName")
            out.append({
                "vendorIdentifier": vendor_id,
                "referenceName": (name_template.format(**values)
                                  if name_template else
                                  reference_name(vendor_id, root)),
                "points": points_for(family, values),
                "showBeforeEarned": bool(family.get("showBeforeEarned", False)),
                "repeatable": bool(family.get("repeatable", False)),
                "localizations": {locale: {"name": "",
                                           "beforeEarnedDescription": "",
                                           "afterEarnedDescription": ""}
                                  for locale in locales},
            })
    return out


def merge(generated: List[dict], existing: List[dict]) -> List[dict]:
    """Keep existing texts and reference names, add the missing ids.

    Points and showBeforeEarned deliberately come from the scheme: they are
    part of the design, not of the copy.
    """
    by_id = {str(e.get("vendorIdentifier")): e for e in existing}
    for item in generated:
        previous = by_id.get(item["vendorIdentifier"])
        if not previous:
            continue
        for locale, values in (previous.get("localizations") or {}).items():
            target = item["localizations"].setdefault(locale, {})
            for key, value in values.items():
                if value:
                    target[key] = value
        for attribute in ("repeatable", "referenceName"):
            if attribute in previous:
                item[attribute] = previous[attribute]
    known = {i["vendorIdentifier"] for i in generated}
    # Ids that exist in ASC or data/ but are not produced by the scheme stay
    # put — deleting them in Game Center would not be reversible anyway.
    for key, item in by_id.items():
        if key not in known:
            generated.append(item)
    return generated


def run(force: bool = False) -> List[str]:
    scheme = load_scheme()
    if not scheme:
        return [f"No scheme at {paths.rel_to_asc(scheme_path())} — nothing to "
                f"generate. Either create one (see the module docstring) or "
                f"maintain data/gamecenter/achievements.json by hand."]
    locales = paths.load_locales()
    generated = build(locales, scheme)
    if not generated:
        return ["The scheme declares no families — nothing to generate."]
    messages = []
    if not force:
        existing = domains.doc_items(domains.load_doc(ACHIEVEMENTS_DOMAIN), ACHIEVEMENTS)
        before = {str(e.get("vendorIdentifier")) for e in existing}
        generated = merge(generated, existing)
        new = [i["vendorIdentifier"] for i in generated
               if i["vendorIdentifier"] not in before]
        messages.append(f"{len(new)} new id(s): {', '.join(new) if new else '—'}")
    if len(generated) > GAME_CENTER_LIMIT:
        messages.append(f"[warn] {len(generated)} achievements exceed the Game Center "
                        f"limit ({GAME_CENTER_LIMIT}) — trim, or ASC will refuse.")
    doc = domains.pack_doc(ACHIEVEMENTS, generated, strip_ids=True)
    path = domains.save_doc(ACHIEVEMENTS_DOMAIN, doc)
    messages.append(f"{len(generated)} achievement(s) -> {paths.rel_to_asc(path)}")
    return messages
