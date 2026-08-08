#!/usr/bin/env python3
"""Replay recorded API traffic — the test the self-test cannot be.

  python3 tests/test_replay.py

The self-test reasons about code in isolation. This one drives `pull` end to
end against a cassette recorded from a real App Store Connect account, with no
credentials and no network. It is the only place where the paths, the
relationship names and the parsing of real response bodies are exercised
together.

The cassette carries structure, not content: every free-text value was replaced
at record time (see core/cassette.py). So the assertions below are about shape
— how many records came back, which keys they carry, what landed in the
readonly block — and never about anybody's marketing copy.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
CASSETTE = os.path.join(HERE, "cassettes", "pull.json")

FAILURES = []


def check(condition, message):
    print(f"  {'ok  ' if condition else 'FAIL'} {message}")
    if not condition:
        FAILURES.append(message)


def scaffold(directory: str) -> None:
    """A minimal project matching the cassette: same bundle id, same locales."""
    data = os.path.join(directory, "data")
    os.makedirs(os.path.join(data, "gamecenter"), exist_ok=True)
    write = lambda rel, obj: open(os.path.join(data, rel), "w").write(
        json.dumps(obj, indent=2))
    write("app.json", {"bundleId": "com.example.app",
                       "idPrefix": "com.example.app.", "platform": "IOS"})
    write("locales.json", {"locales": ["de-DE", "en-US", "es-ES"]})
    write("accessibility.json", {"resource": "accessibilityDeclarations",
                                 "key": "deviceFamily", "items": []})
    write("iap.json", {"resource": "inAppPurchases", "key": "productId", "items": []})
    for name, resource, key in (
            ("gamecenter/achievements.json", "gameCenterAchievements", "vendorIdentifier"),
            ("gamecenter/leaderboards.json", "gameCenterLeaderboards", "vendorIdentifier"),
            ("gamecenter/leaderboard_sets.json", "gameCenterLeaderboardSets", "vendorIdentifier")):
        write(name, {"resource": resource, "key": key, "items": []})


def run_pull(directory: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["ASCSYNC_CASSETTE"] = CASSETTE
    env.pop("ASCSYNC_CASSETTE_MODE", None)
    # Deliberately unset: replay must work without them, or CI cannot run it.
    for name in ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY_PATH"):
        env.pop(name, None)
    env["PYTHONPATH"] = os.path.join(ROOT, "src")
    return subprocess.run(
        [sys.executable, "-m", "ascsync", "pull", "--snapshot-only",
         "--domain", "accessibility", "--domain", "iap", "--domain", "gamecenter"],
        cwd=directory, env=env, capture_output=True, text=True)


def main() -> int:
    if not os.path.exists(CASSETTE):
        print(f"No cassette at {CASSETTE} — record one first (see core/cassette.py).")
        return 1

    directory = tempfile.mkdtemp(prefix="ascsync-replay-")
    try:
        scaffold(directory)
        print("== Replay: pull without credentials ==")
        result = run_pull(directory)
        check(result.returncode == 0,
              f"pull exits 0 (stderr: {result.stderr.strip()[:120] or 'none'})")

        snapshot = os.path.join(directory, ".snapshot")
        check(os.path.isdir(snapshot), "a snapshot was written")

        def load(*parts):
            path = os.path.join(snapshot, *parts)
            return json.load(open(path)) if os.path.exists(path) else None

        print("\n== What came back has the right shape ==")
        access = load("accessibility.json")
        check(access and len(access["items"]) == 2,
              f"accessibility: {len(access['items']) if access else 0} device families")
        families = {i.get("deviceFamily") for i in (access or {}).get("items", [])}
        check(families == {"IPHONE", "IPAD"}, f"both families present -> {sorted(families)}")
        check(all("state" in (i.get("readonly") or {}) for i in access["items"]),
              "'state' is held in the readonly block, not among the writable fields")

        iap = load("iap.json")
        check(iap and len(iap["items"]) == 6, f"iap: {len(iap['items']) if iap else 0} products")
        check(all("productId" in i for i in (iap or {}).get("items", [])),
              "every product carries its natural key")
        check(any(i.get("localizations") for i in (iap or {}).get("items", [])),
              "localizations came through as children")

        achievements = load("gamecenter", "achievements.json")
        check(achievements and len(achievements["items"]) == 91,
              f"achievements: {len(achievements['items']) if achievements else 0}")
        locales = set()
        for item in (achievements or {}).get("items", []):
            locales |= set((item.get("localizations") or {}).keys())
        check(locales == {"de-DE", "en-US", "es-ES"},
              f"all three languages arrived -> {sorted(locales)}")

        boards = load("gamecenter", "leaderboards.json")
        check(boards and len(boards["items"]) == 7,
              f"leaderboards: {len(boards['items']) if boards else 0}")
        recurring = [b for b in (boards or {}).get("items", [])
                     if b.get("recurrenceStartDate")]
        check(len(recurring) == 3,
              f"{len(recurring)} boards carry a recurrence — the field survives the pull")

        print("\n== Nothing of the recording's origin leaks ==")
        blob = json.dumps([access, iap, achievements, boards], ensure_ascii=False)
        check("8shape" not in blob.lower(), "no trace of the app it was recorded from")
    finally:
        shutil.rmtree(directory, ignore_errors=True)

    print("")
    if FAILURES:
        print(f"{len(FAILURES)} check(s) failed:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("Replay passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
