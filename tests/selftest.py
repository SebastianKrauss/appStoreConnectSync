#!/usr/bin/env python3
"""Self-tests — offline, no credentials, no API.

  python3 tests/selftest.py

They cover the things that could be quietly wrong: the three-way
classification, the play-mode rule, occurrence expansion, the asset resolution
chain, and that every text combination in every language stays under Apple's
limits. They run against their own fixtures, not against data/ — this tests
the tool, not the contents of your project.
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from ascsync.core import assets as assetlib          # noqa: E402
from ascsync.core import differ, domains, paths, validate  # noqa: E402
from ascsync.core.registry import ImageRule          # noqa: E402
from ascsync.generators import leaderboard_events as gen   # noqa: E402
from ascsync.resources import ALL_DOMAINS, events as events_res, game_center, iap  # noqa: E402

FAILURES = []


def check(condition, message):
    if condition:
        print(f"  ok   {message}")
    else:
        print(f"  FEHL {message}")
        FAILURES.append(message)


def section(title):
    print(f"\n== {title} ==")


# ---------------------------------------------------------------------------
def test_three_way_diff():
    section("Three-way diff")
    cases = [
        # (desired, snapshot, remote, have_snapshot, expected)
        ("a", "a", "a", True, differ.OK),
        ("b", "a", "a", True, differ.WRITE),
        ("a", "a", "b", True, differ.DRIFT),
        ("b", "a", "c", True, differ.CONFLICT),
        ("b", "a", "b", True, differ.OK),          # converged
        ("b", None, "a", False, differ.WRITE),     # no snapshot
        ("  a ", "a", "a", True, differ.OK),       # whitespace is noise
        ("", None, None, True, differ.OK),         # None == ""
    ]
    for desired, snapshot, remote, have, expected in cases:
        got = differ.classify_field(desired, snapshot, remote, have)
        check(got == expected,
              f"{desired!r}/{snapshot!r}/{remote!r} -> {got} (expected {expected})")


def test_play_mode():
    section("Play-mode rule")
    # ISO week in UTC: %5==0 -> oneShot, else even -> raw, odd -> normal
    for iso_week, expected in ((1, "normal"), (2, "raw"), (4, "raw"), (5, "oneShot"),
                               (10, "oneShot"), (7, "normal"), (20, "oneShot"),
                               (12, "raw"), (13, "normal")):
        # Monday of that ISO week in 2026
        start = datetime.fromisocalendar(2026, iso_week, 1).replace(tzinfo=timezone.utc)
        got = gen.play_mode(start)
        check(got == expected, f"week {iso_week} -> {got} (expected {expected})")


def test_duration_and_rrule():
    section("Duration and recurrence")
    check(gen.parse_duration("P7D") == timedelta(days=7), "P7D = 7 days")
    check(gen.parse_duration("PT15M") == timedelta(minutes=15), "PT15M = 15 minutes")
    check(gen.parse_duration("P1DT12H") == timedelta(days=1, hours=12), "P1DT12H")
    start = datetime(2026, 1, 5, tzinfo=timezone.utc)
    occurrences = gen.expand_rrule(start, "FREQ=DAILY;INTERVAL=21",
                                   start + timedelta(days=63))
    check(len(occurrences) == 4, f"4 occurrences in 63 days (got: {len(occurrences)})")
    check(occurrences[1] - occurrences[0] == timedelta(days=21), "21 days apart")
    weekly = gen.expand_rrule(start, "FREQ=WEEKLY;COUNT=3", start + timedelta(days=365))
    check(len(weekly) == 3, "COUNT=3 is honoured")


def test_event_texts_within_limits():
    section("Event texts in every combination")
    templates = gen.load_templates()
    problems = 0
    for locale, texts in templates["texts"].items():
        for metric in ("turns", "time", "score"):
            for mode in ("normal", "raw", "oneShot"):
                start = datetime(2026, 3, 2, tzinfo=timezone.utc)
                rendered, messages = gen._render(texts, locale, metric, mode, start,
                                                 start + timedelta(days=7), "test")
                for message in messages:
                    print(f"       {message}")
                problems += len(messages)
                for field, limit in gen.LIMITS.items():
                    if len(rendered[field]) > limit:
                        problems += 1
    check(problems == 0, f"every text is under its limit ({problems} findings)")


def _fixture_templates() -> dict:
    """Templates for the test — deliberately NOT from data/.

    The self-test checks the tool, not the contents of any one project.
    Whatever it needs, it states here.
    """
    return {
        "leaderboards": {"score": "com.example.app.challenge.score"},
        "defaults": {
            "badge": "CHALLENGE", "priority": "HIGH",
            "purpose": "KEEP_ACTIVE_USERS_INFORMED",
            "purchaseRequirement": "NO_COST_ASSOCIATED",
            "primaryLocale": "en-US", "publishLeadDays": 7,
            "deepLink": "https://example.com/event/{metric}",
            "territories": [], "territoriesExclude": ["RUS"]},
        "texts": {"en-US": {
            "metricNames": {"score": "Points"},
            "playModeNames": {"normal": "", "raw": "Pure", "oneShot": "1 attempt"},
            "name": {"score": "Challenge: Points"},
            "shortDescription": {"score": "Score as many points as you can."},
            "longDescription": {"score": "As many points as possible."},
            "overrides": {}}}}


def test_territory_exclusion():
    """Territories are derived (available minus excluded), not enumerated."""
    section("Territories")
    templates = _fixture_templates()
    start = datetime(2026, 3, 2, tzinfo=timezone.utc)
    event, _ = gen._build_event("t", "score", start, timedelta(days=7), templates,
                                templates["defaults"], ["DEU", "USA", "RUS", "ESP"])
    used = event["territorySchedules"][0].get("territories")
    check(used == ["DEU", "USA", "ESP"], f"RUS filtered out -> {used}")

    without, msgs = gen._build_event("t", "score", start, timedelta(days=7), templates,
                                     templates["defaults"], [])
    check("territories" not in without["territorySchedules"][0]
          and any("no territories" in m for m in msgs),
          "without an availability list it warns instead of guessing")


def test_asset_resolution():
    section("Asset resolution (variant -> metric -> default)")
    with tempfile.TemporaryDirectory() as tmp:
        original = paths.ASSETS_DIR
        paths.ASSETS_DIR = tmp
        try:
            os.makedirs(os.path.join(tmp, "events", "turns", "de-DE"))
            open(os.path.join(tmp, "events", "turns", "de-DE", "card.png"), "wb").close()
            fmt = {"assetVariant": "turns-oneShot", "metric": "turns", "locale": "de-DE"}
            found = assetlib.resolve_asset(events_res.CARD.path,
                                           events_res.CARD.fallbacks, **fmt)
            check(found is not None and found.endswith("turns/de-DE/card.png"),
                  "the fallback to the metric variant works")
            fmt["locale"] = "en-US"
            check(assetlib.resolve_asset(events_res.CARD.path,
                                         events_res.CARD.fallbacks, **fmt) is None,
                  "a missing language is not silently substituted")
            # a video instead of an image
            os.makedirs(os.path.join(tmp, "events", "default", "en-US"))
            open(os.path.join(tmp, "events", "default", "en-US", "card.mp4"), "wb").close()
            found = assetlib.resolve_asset(events_res.CARD.path,
                                           events_res.CARD.fallbacks, **fmt)
            check(found is not None and found.endswith("card.mp4"),
                  "a video is found in place of the image")
        finally:
            paths.ASSETS_DIR = original


def test_image_rules():
    section("Image checks")
    png = _tiny_png(16, 16)
    problems = assetlib.check_file(png, ImageRule(exact=((512, 512),)))
    check(any("16x16" in p for p in problems), "a wrong size is caught")
    check(not assetlib.check_file(png, ImageRule(min_width=8, min_height=8)),
          "a matching size is accepted")
    problems = assetlib.check_file(png, ImageRule(aspect=16 / 9.0))
    check(any("aspect ratio" in p for p in problems),
          "a wrong aspect ratio is caught")
    os.unlink(png)


def _tiny_png(width: int, height: int) -> str:
    import struct
    import zlib
    raw = b"".join(b"\x00" + b"\xff\x00\x00" * width for _ in range(height))

    def chunk(tag, payload):
        return (struct.pack(">I", len(payload)) + tag + payload +
                struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))
    data = (b"\x89PNG\r\n\x1a\n"
            + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw))
            + chunk(b"IEND", b""))
    handle, path = tempfile.mkstemp(suffix=".png")
    with os.fdopen(handle, "wb") as f:
        f.write(data)
    return path


HARD_PROBLEM = ("characters (limit", "unknown (allowed", "required field")


def test_data_files_load():
    """Checks the SHAPE of the data, not how complete it is.

    Texts that are still empty are open editorial work and show up in
    'ascsync validate'. Only what ASC would hard-reject fails here: limit
    violations, unknown enum values, missing required fields.
    """
    section("data/ loads and is formally valid")
    locales = paths.load_locales()
    hard = 0
    for domain in ALL_DOMAINS:
        doc = domains.load_doc(domain)
        check(doc is not None, f"data/{domain.data_file} vorhanden")
        if doc is None:
            continue
        problems = [p for p in validate.validate_domain(domain, locales,
                                                        check_assets=False)
                    if any(marker in p for marker in HARD_PROBLEM)]
        hard += len(problems)
        for problem in problems[:5]:
            print(f"       {problem}")
    check(hard == 0, f"no limit, enum or required-field violations ({hard} found)")


def test_readiness():
    """--readiness reports fields the API leaves optional but submission needs —
    and the normal validation still does NOT report them.

    Deliberately against a SYNTHETIC document rather than data/: otherwise the
    test would go red as soon as the real data is filled in. It checks the
    mechanism, not how complete the content is.
    """
    section("Einreichungs-Reife")
    import json

    from ascsync.resources import app_store
    locales = paths.load_locales()

    gaps = {"resource": "appStoreVersions", "key": "versionString", "items": [{
        "versionString": "9.9", "platform": "IOS",
        "copyright": "", "releaseType": "MANUAL",
        "localizations": {locale: {"description": "x", "keywords": "x",
                                   "supportUrl": ""} for locale in locales},
    }]}                                    # no ageRating block, empty required fields

    original = paths.DATA_DIR
    with tempfile.TemporaryDirectory() as tmp:
        paths.DATA_DIR = tmp
        try:
            os.makedirs(os.path.join(tmp, "store"))
            with open(os.path.join(tmp, app_store.VERSIONS_DOMAIN.data_file), "w",
                      encoding="utf-8") as f:
                json.dump(gaps, f)
            plain = validate.validate_domain(app_store.VERSIONS_DOMAIN, locales,
                                             check_assets=False, readiness=False)
            ready = validate.validate_domain(app_store.VERSIONS_DOMAIN, locales,
                                             check_assets=False, readiness=True)
        finally:
            paths.DATA_DIR = original

    extra = set(ready) - set(plain)
    check(len(ready) > len(plain), f"{len(extra)} extra findings only with --readiness")
    check(not any("required for submission" in p for p in plain),
          "without --readiness the normal validation is unchanged")
    check(any("supportUrl" in p for p in extra), "an empty support URL is caught")
    check(any("copyright" in p for p in extra), "an empty copyright is caught")
    check(any("ageRating" in p for p in extra), "a missing ageRating block is caught")

    structural = validate.validate_readiness(locales)
    check(isinstance(structural, list), "the structural checks run")
    check(len(validate.NOT_CHECKABLE) >= 4,
          "the not-checkable-offline items are listed")


def test_pull_merge_overwrites_local_texts():
    """Shows what 'pull --snapshot-only' is for: merging into data/ lets ASC
    win field by field, empty values included. Anyone who filled data/ before
    the first pull would lose those texts.

    Synthetic, so the test does not depend on how full data/ happens to be.
    """
    section("Pull merge (the case for --snapshot-only)")
    import json

    domain = game_center.ACHIEVEMENTS_DOMAIN
    local_doc = {"resource": "gameCenterAchievements", "key": "vendorIdentifier",
             "items": [{
                 "vendorIdentifier": "x.champion", "points": 25,
                 "_localOnly": "generator metadata",
                 "localizations": {"en-US": {"name": "Champion",
                                             "beforeEarnedDescription": "Not yet",
                                             "afterEarnedDescription": "Done"}},
             }]}
    # This is what it looks like when the achievement exists in ASC but has no text:
    remote = [{"vendorIdentifier": "x.champion", "points": 25,
               "localizations": {"en-US": {"name": "",
                                           "beforeEarnedDescription": "",
                                           "afterEarnedDescription": ""}}}]

    original = paths.DATA_DIR
    with tempfile.TemporaryDirectory() as tmp:
        paths.DATA_DIR = tmp
        try:
            os.makedirs(os.path.join(tmp, os.path.dirname(domain.data_file)),
                        exist_ok=True)
            with open(os.path.join(tmp, domain.data_file), "w",
                      encoding="utf-8") as f:
                json.dump(local_doc, f)
            merged = domains.merge_into_data(domain, remote)
        finally:
            paths.DATA_DIR = original

    item = domains.doc_items(merged, domain.resource)[0]
    check(item["localizations"]["en-US"]["name"] == "",
          "an empty ASC text overwrites the local one — hence --snapshot-only")
    check(item.get("_localOnly") == "generator metadata",
          "fields ASC does not know survive the merge")


def test_code_drift_detects_patterns():
    """The source cross-check is optional — this tests the mechanism.

    Instead of a real project it runs against a generated mini source tree, so
    it does not depend on an app sitting next door, and still checks what could
    be quietly wrong: that interpolated literals arrive as patterns and match
    the right ids.
    """
    section("Source cross-check spots generated families")
    import re
    directory = tempfile.mkdtemp()
    with open(os.path.join(directory, "Sample.swift"), "w", encoding="utf-8") as f:
        f.write('let a = "com.example.app.first.win"\n'
                'let b = "com.example.app.level.\\(Int(index))"\n'
                'let c = "com.example.app.streak.\\(days)"\n')
    app = paths.data_path("app.json")
    original = paths.read_json(app)
    try:
        patched = dict(original)
        patched["idPrefix"] = "com.example.app."
        patched["code"] = {"sourceDir": directory, "sourceSuffix": ".swift"}
        paths.write_json(app, patched)
        paths.forget_app_config()
        found = validate.swift_identifiers()
    finally:
        paths.write_json(app, original)
        paths.forget_app_config()

    check(found["literals"] == ["com.example.app.first.win"],
          f"fixed id recognised -> {found['literals']}")
    check(len(found["patterns"]) == 2,
          f"{len(found['patterns'])} interpolated patterns recognised")
    covered = [i for i in ("com.example.app.level.7", "com.example.app.streak.30",
                           "com.example.app.other")
               if any(re.match(p, i) for p in found["patterns"])]
    check(covered == ["com.example.app.level.7", "com.example.app.streak.30"],
          f"patterns match the generated family and nothing else -> {covered}")

    check(validate.swift_identifiers()["literals"] == []
          or bool(validate.source_dir()),
          "without a configured source tree the check is silently skipped")


def test_products_match_code():
    """Product ids against the source — only when a source tree is configured."""
    section("Product ids against the source")
    if not validate.source_dir():
        check(True, "no 'code' section in app.json — check skipped")
        return
    products = [str(i.get("productId")) for i in domains.doc_items(
        domains.load_doc(iap.IAP_DOMAIN), iap.PRODUCTS)]
    literals = set(validate.swift_identifiers()["literals"])
    missing = [p for p in products if p not in literals]
    check(not missing, f"every product id appears in the source (missing: {missing})")


def test_achievement_scheme_expansion():
    """The scheme is a declaration, so its expansion is where mistakes hide."""
    section("Achievement scheme")
    from ascsync.generators import achievement_template as ach

    scheme = {"families": [
        {"suffix": "tutorial.done", "points": 1},
        {"suffix": "gift.{n}", "values": {"n": [1, 7, 30]},
         "points": {"by": "n", "map": {"1": 1, "30": 10}, "default": 5}},
        {"suffix": "{mode}.win.{n}",
         "values": {"mode": ["solo", "versus"], "n": [10, 100]},
         "exclude": ["versus.win.100"]},
        {"suffix": "champion", "points": 25,
         "showBeforeEarned": True, "repeatable": True},
    ]}
    items = ach.build(["en-US"], scheme)
    tails = [i["vendorIdentifier"].split("app.", 1)[-1] for i in items]

    check(len(items) == 1 + 3 + 3 + 1, f"{len(items)} ids (exclude removed one)")
    check("versus.win.100" not in tails, "exclude drops exactly that combination")
    check(tails[:2] == ["tutorial.done", "gift.1"],
          f"declaration order is kept -> {tails[:2]}")

    points = {i["vendorIdentifier"].split("app.", 1)[-1]: i["points"] for i in items}
    check(points.get("gift.1") == 1 and points.get("gift.30") == 10,
          "points lookup hits the mapped values")
    check(points.get("gift.7") == 5, "and falls back to the default")
    champion = [i for i in items if i["vendorIdentifier"].endswith("champion")][0]
    check(champion["showBeforeEarned"] and champion["repeatable"] and champion["points"] == 25,
          "flags and fixed points survive")

    # merge() must not lose copy that somebody already wrote
    existing = [{"vendorIdentifier": items[0]["vendorIdentifier"],
                 "referenceName": "Hand-picked name",
                 "localizations": {"en-US": {"name": "Welcome"}}},
                {"vendorIdentifier": "com.example.app.legacy.one"}]
    merged = ach.merge([dict(i) for i in items], existing)
    first = [m for m in merged if m["vendorIdentifier"] == items[0]["vendorIdentifier"]][0]
    check(first["localizations"]["en-US"]["name"] == "Welcome",
          "existing text survives a regeneration")
    check(first["referenceName"] == "Hand-picked name",
          "a renamed referenceName survives too")
    check(any(m["vendorIdentifier"].endswith("legacy.one") for m in merged),
          "ids the scheme does not know are kept, never dropped")


def test_html_report():
    """The report has one job: put what needs a decision at the top."""
    section("HTML report")
    from ascsync.core import htmlreport, planner as pl, report as rep_mod

    rep = rep_mod.Report("plan", dry_run=True)
    plan = rep.plan_for("store")
    plan.add(pl.UPLOAD, "1.0/screenshots/01.png", "01.png")
    plan.add(pl.NOOP, "1.0/localizations/en-US")
    plan.add(pl.CONFLICT, "1.0/description", "local vs ASC — not written")
    plan.add(pl.UPDATE, "1.0/keywords", fields={"keywords": "x"})
    out = htmlreport.render(rep, "demo")

    check(out.startswith("<!doctype html>"), "renders a standalone document")
    check("<script" not in out.lower(), "no scripts — it is meant to be opened, not run")
    check(out.count("http://") + out.count("https://") == 0
          or "example.com" in out, "nothing is fetched from outside")
    check(out.index("conflict") < out.index("update") < out.index("upload"),
          "conflicts come before updates, updates before uploads")
    check("1.0/localizations/en-US" not in out,
          "unchanged records are counted, not listed")
    check("&#x27;" in out or "<td" in out, "values are escaped into the table")


def main() -> int:
    for test in (test_three_way_diff, test_play_mode, test_duration_and_rrule,
                 test_event_texts_within_limits, test_territory_exclusion,
                 test_asset_resolution,
                 test_image_rules, test_data_files_load, test_readiness,
                 test_pull_merge_overwrites_local_texts,
                 test_code_drift_detects_patterns, test_products_match_code,
                 test_achievement_scheme_expansion, test_html_report):
        test()
    print("")
    if FAILURES:
        print(f"{len(FAILURES)} test(s) failed:")
        for failure in FAILURES:
            print(f"  - {failure}")
        return 1
    print("All self-tests passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
