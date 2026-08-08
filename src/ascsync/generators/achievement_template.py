"""Build an achievement scaffold from an id scheme.

    !! THIS IS AN EXAMPLE, NOT A GENERAL-PURPOSE GENERATOR !!

The rules below mirror the achievement families of one particular game
(tutorial, daily gifts, wins, shape types, star ratings). They are here to show
the shape of the thing: a generator does NOT read your source, it reproduces
the scheme, and `ascsync validate` then tells you whether the two still agree.

For your own project, rewrite `build()` — or delete this file and maintain
data/gamecenter/achievements.json by hand. Everything the generator does
afterwards (merging without losing texts, the 100-achievement limit, writing
the document) is generic and worth keeping.

  ascsync achievements template            # add missing ids, keep the texts
  ascsync achievements template --force    # regenerate all (texts are lost!)
"""
from __future__ import annotations

from typing import List, Optional

from ..core import domains, paths
from ..resources.game_center import ACHIEVEMENTS, ACHIEVEMENTS_DOMAIN

SHAPE_TYPES = ["circle", "triangle", "square", "pentagon", "hexagon",
               "heptagon", "octagon", "plus", "star", "random"]
GAME_CENTER_LIMIT = 100


def prefix() -> str:
    return (paths.load_app_config().get("idPrefix") or "").rstrip(".")


def reference_name(vendor_id: str, root: str) -> str:
    return vendor_id[len(root) + 1:].replace(".", " ").title()


def points_for(vendor_id: str, root: str) -> int:
    """Points by difficulty: easy=1, medium=5, hard or long-haul=10."""
    tail = vendor_id[len(root) + 1:]
    if tail == "tutorial.completed":
        return 1
    if tail.startswith("gift."):
        return {1: 1, 3: 1, 7: 5, 14: 5, 30: 10}.get(int(tail.split(".")[1]), 5)
    if tail in ("solo.first.win", "vs.first.win"):
        return 1
    if tail in ("solo.win.10", "vs.win.10"):
        return 5
    if tail in ("solo.win.100", "vs.win.100"):
        return 10
    if tail.startswith("trickshot.stars."):
        return {1: 1, 10: 5, 25: 5, 50: 10, 90: 10}.get(int(tail.rsplit(".", 1)[1]), 5)
    if tail.startswith("combo."):
        return {2: 1, 3: 5, 4: 10}.get(int(tail.rsplit(".", 1)[1]), 5)
    if tail.startswith("turns."):
        n = int(tail.rsplit(".", 1)[1])
        return 1 if n in (100, 75, 50) else 5 if n in (30, 25, 20) else 10
    if tail.startswith("duration."):
        n = int(tail.rsplit(".", 1)[1])
        return 1 if n in (60, 50, 40) else 5 if n in (30, 25, 20) else 10
    return {"1": 1, "10": 5, "100": 10}.get(tail.rsplit(".", 1)[1], 5)


def _make(vendor_id: str, root: str, locales: List[str],
          points: Optional[int] = None, show_before_earned: bool = False,
          repeatable: bool = False) -> dict:
    return {
        "vendorIdentifier": vendor_id,
        "referenceName": reference_name(vendor_id, root),
        "points": points if points is not None else points_for(vendor_id, root),
        "showBeforeEarned": show_before_earned,
        "repeatable": repeatable,
        "localizations": {locale: {"name": "", "beforeEarnedDescription": "",
                                   "afterEarnedDescription": ""}
                          for locale in locales},
    }


def build(locales: List[str]) -> List[dict]:
    root = prefix()
    out: List[dict] = []

    def add(suffix: str, points: Optional[int] = None,
            show_before_earned: bool = False, repeatable: bool = False):
        out.append(_make(f"{root}.{suffix}", root, locales, points,
                         show_before_earned, repeatable))

    add("tutorial.completed")
    for n in (1, 3, 7, 14, 30):
        add(f"gift.{n}")
    # Reused across ALL challenges, hence visible before it is earned and
    # repeatable. In the source project the point value doubles as the size of
    # the reward, which the app reads back from ASC.
    add("event.champion", points=25, show_before_earned=True, repeatable=True)
    for kind in ("solo", "vs"):
        add(f"{kind}.first.win")
        add(f"{kind}.win.10")
        add(f"{kind}.win.100")
    for n in (1, 10, 100):
        add(f"zero.foul.{n}")
    for n in (1, 10, 100):
        add(f"only.correct.shapes.in.pocket.{n}")
    for n in (10, 15, 20, 25, 30, 50, 75, 100):
        add(f"turns.{n}")
    for n in (10, 15, 20, 25, 30, 40, 50, 60):
        add(f"duration.{n}")
    for n in (1, 10, 25, 50, 90):
        add(f"trickshot.stars.{n}")
    for n in (1, 10, 100):
        add(f"hole.in.one.{n}")
    for n in (2, 3, 4):
        add(f"combo.right.suit.{n}")
    for n in (2, 3, 4):
        add(f"combo.wrong.suit.{n}")
    for n in (1, 10, 100):
        add(f"only.eight.shape.in.pocket.{n}")
    for n in (1, 10, 100):
        add(f"cue.shape.in.pocket.{n}")
    # 'random' has no achievements of its own, and the .1 tier is dropped to
    # stay under the Game Center limit of 100 -> 9 types x {10, 100}.
    for shape in [t for t in SHAPE_TYPES if t != "random"]:
        for n in (10, 100):
            out.append(_make(f"{root}.cue.shape.type.{shape}.{n}", root, locales))
    for shape in [t for t in SHAPE_TYPES if t != "random"]:
        for n in (10, 100):
            out.append(_make(f"{root}.shape.type.{shape}.{n}", root, locales))
    return out


def merge(generated: List[dict], existing: List[dict]) -> List[dict]:
    """Keep existing texts and reference names, add the missing ids.

    Points and showBeforeEarned deliberately come from the generator rules.
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
    # Ids that exist in ASC or data/ but are not produced by the generator stay
    # put — deleting them in Game Center would not be reversible anyway.
    for key, item in by_id.items():
        if key not in known:
            generated.append(item)
    return generated


def run(force: bool = False) -> List[str]:
    locales = paths.load_locales()
    generated = build(locales)
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
    messages.append(f"{len(generated)} Achievements -> {paths.rel_to_asc(path)}")
    return messages
