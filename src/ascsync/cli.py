#!/usr/bin/env python3
"""ascsync — one command for App Store Connect.

  ascsync init --bundle-id com.example.app   create a project here and fill it
  ascsync doctor                       auth, role, rate limit, app/version state
  ascsync pull   [--domain …] [--all]  ASC state into data/ + .snapshot/
                 [--snapshot-only]     .snapshot/ only, data/ left untouched
  ascsync plan   [--domain …] [--html report.html]
                                       three-way diff, writes nothing (exit 2 on drift)
  ascsync push   [--domain …] --yes    write (WITHOUT --yes: dry run)
  ascsync validate [--domain …]        offline: limits, assets, languages, code drift
  ascsync events generate [--ahead 12w]  occurrence drafts + asset to-dos
  ascsync privacy publish [--yes]      publish app privacy (its own step)
  ascsync submit --version 1.0 | --event <ref> [--yes]
  ascsync releases [--yes]             release achievements/leaderboards

Credentials come from the environment:
  ASC_ISSUER_ID, ASC_KEY_ID, ASC_PRIVATE_KEY_PATH

The rule: `push` without `--yes` is a dry run. ALWAYS `pull` before the first
push, or there is no snapshot and the three-way diff cannot spot changes made
by someone else.
"""
from __future__ import annotations

import argparse
import os
import sys

from ascsync.core import assets as assetlib          # noqa: E402
from ascsync.core import client as clientlib         # noqa: E402
from ascsync.core import (domains, htmlreport, paths, planner, report,  # noqa: E402
                          validate)
from ascsync.core.auth import MissingCredentials, missing_env       # noqa: E402
from ascsync.core.engine import Engine               # noqa: E402
from ascsync.generators import leaderboard_events    # noqa: E402
from ascsync.resources import (ALL_DOMAINS, app_store, events as events_res,
                           game_center, iap as iap_res, privacy as privacy_res,
                           select, submission)   # noqa: E402


# ---------------------------------------------------------------------------
def build_context(dry_run: bool, args) -> domains.Context:
    app = paths.load_app_config()
    try:
        client = clientlib.Client(dry_run=dry_run, verbose=getattr(args, "verbose", False))
    except MissingCredentials as e:
        raise SystemExit(str(e))
    app_id = app.get("appId") or clientlib.resolve_app_id(client, app["bundleId"])
    return domains.Context(
        client=client, app_id=app_id, app=app, locales=paths.load_locales(),
        version=getattr(args, "version", None),
        flags={"allow_pricing": getattr(args, "allow_pricing", False),
               "allow_pages": getattr(args, "allow_pages", False)},
    )


def make_engine(ctx: domains.Context, args, lock: assetlib.AssetLock) -> Engine:
    return Engine(ctx.client, None, lock=lock,
                  skip_assets=getattr(args, "skip_assets", False),
                  only_keys=getattr(args, "only", []) or (),
                  only_locales=getattr(args, "only_locale", []) or ())


# ---------------------------------------------------------------------------
def cmd_doctor(args) -> int:
    missing = missing_env()
    print("ascsync doctor")
    print(f"  Environment: {clientlib.env_summary()}")
    if missing:
        print("  -> Without these variables there is no API access. "
              "See README.md.")
        return 1
    app = paths.load_app_config()
    ctx = build_context(dry_run=True, args=args)
    print(f"  App: {ctx.app_id} ({app['bundleId']})")
    print(f"  Languages: {', '.join(ctx.locales)}")
    versions = ctx.client.get_all(f"/v1/apps/{ctx.app_id}/appStoreVersions",
                                  **{"filter[platform]": app_store.PLATFORM})
    for version in versions[:5]:
        attributes = version.get("attributes", {}) or {}
        state = attributes.get("appStoreState") or attributes.get("appVersionState")
        editable = "editable" if state in app_store.EDITABLE_STATES else "locked"
        print(f"  Version {attributes.get('versionString')}: {state} ({editable})")
    if not versions:
        print("  [warn] No version in ASC — version texts and screenshots need one.")
    info_id = app_store.resolve_app_info_id(ctx)
    print(f"  App info: {'editable (' + info_id + ')' if info_id else 'NOT editable'}")
    try:
        parent = game_center.resolve_parent(ctx)
        print(f"  Game Center: {parent[0]} {parent[1]}")
    except SystemExit as e:
        print(f"  [warn] Game Center: {e}")
    print(f"  Rate limit: {ctx.client.rate_remaining} calls left this hour"
          if ctx.client.rate_remaining is not None else "  Rate limit: unknown")
    print(f"  Snapshot: {'present' if os.path.isdir(paths.SNAPSHOT_DIR) else 'missing'}"
          f" ({paths.rel_to_asc(paths.SNAPSHOT_DIR)})")
    return 0


def cmd_init(args) -> int:
    """Create a project here, and fill it from an app that already exists.

    The scaffold is the easy half. The hard half is that a new user has no idea
    which of the twelve files matter, so this writes only what is needed to get
    going, then pulls the real state on top and says what to do next.
    """
    root = paths.PROJECT_ROOT
    app_path = paths.APP_PATH
    if os.path.exists(app_path) and not args.force:
        print(f"{paths.rel_to_asc(app_path)} already exists — "
              f"nothing done. Use --force to overwrite it.")
        return 1
    if not args.bundle_id:
        print("Which app? Pass --bundle-id com.example.yourapp")
        return 1

    locales = [l.strip() for l in (args.locales or "en-US").split(",") if l.strip()]
    paths.write_json(app_path, {
        "_comment": ["bundleId is required; everything else is optional.",
                     "idPrefix is stripped from asset filenames.",
                     "Add a 'code' block to cross-check ids against your source "
                     "— see the docstring in core/validate.py."],
        "bundleId": args.bundle_id,
        "idPrefix": args.bundle_id + ".",
        "platform": "IOS",
        "primaryLocale": locales[0],
        "categories": {"primaryCategory": "", "primarySubcategoryOne": "",
                       "primarySubcategoryTwo": "", "secondaryCategory": ""},
    })
    paths.write_json(paths.LOCALES_PATH, {
        "_comment": ["One language list for ALL domains. Adding one here "
                     "demands it everywhere: store texts, achievements, "
                     "leaderboards, purchases, events."],
        "locales": locales,
    })
    for directory in ("assets/screenshots", "assets/previews",
                      "assets/gamecenter/achievements", "assets/gamecenter/leaderboards",
                      "assets/iap/review", "assets/subscriptions/review",
                      "assets/events", ".snapshot"):
        os.makedirs(os.path.join(root, directory), exist_ok=True)
    print(f"Wrote {paths.rel_to_asc(app_path)} and "
          f"{paths.rel_to_asc(paths.LOCALES_PATH)}, and created assets/.")

    if args.no_pull:
        print("\nNext: 'ascsync pull' to fill data/ from App Store Connect.")
        return 0
    if missing_env():
        print("\nNo credentials in the environment, so nothing was fetched.")
        print("Set ASC_ISSUER_ID, ASC_KEY_ID and ASC_PRIVATE_KEY_PATH "
              "(see the README), then run 'ascsync pull'.")
        return 0

    # A full pull is right here and nowhere else: data/ is empty, so there is
    # no local text an empty ASC field could overwrite.
    print("\nFetching the current state from App Store Connect …")
    args.domain, args.snapshot_only, args.only = [], False, []
    args.only_locale, args.skip_assets, args.all = [], False, True
    code = cmd_pull(args)
    if code == 0:
        print("\nNow: read through data/, then 'ascsync validate --readiness' "
              "to see what submission still needs.")
    return code


def cmd_pull(args) -> int:
    rep = report.Report("pull", dry_run=False)
    ctx = build_context(dry_run=True, args=args)   # pull never writes to ASC
    lock = assetlib.AssetLock()
    engine = make_engine(ctx, args, lock)
    for domain in select(args.domain):
        plan = rep.plan_for(domain.name)
        print(f"\n== {domain.title or domain.name} ==")
        try:
            items = (domain.pull_fn(engine, ctx, domain) if domain.pull_fn
                     else domains.generic_pull(engine, ctx, domain))
        except clientlib.ApiError as e:
            rep.fail(f"{domain.name}: {e}")
            continue
        snapshot_doc = domains.pack_doc(domain.resource, items, strip_ids=False)
        path = domains.save_doc(domain, snapshot_doc, snapshot=True)
        if not args.snapshot_only:
            merged = domains.merge_into_data(domain, items)
            path = domains.save_doc(domain, merged)
        plan.add(planner.NOOP, domain.name, f"{len(items)} record(s) -> "
                                            f"{paths.rel_to_asc(path)}")
        print(f"  {len(items)} record(s) -> {paths.rel_to_asc(path)}")
    if args.snapshot_only:
        print("\ndata/ was left alone — 'ascsync plan' now shows field by field "
              "where ASC and data/ diverge.")
    rep.summary()
    rep.write_json()
    return 1 if rep.failed else 0


def _run_apply(args, dry_run: bool, command: str) -> int:
    rep = report.Report(command, dry_run=dry_run)
    ctx = build_context(dry_run=dry_run, args=args)
    lock = assetlib.AssetLock()
    engine = make_engine(ctx, args, lock)
    for domain in select(args.domain):
        plan = rep.plan_for(domain.name)
        print(f"\n== {domain.title or domain.name} ==")
        if domain.push_flag and not ctx.flags.get(domain.push_flag):
            plan.add(planner.BLOCKED, domain.name,
                     f"braucht --{domain.push_flag.replace('_', '-')}")
            continue
        try:
            if domain.apply_fn:
                domain.apply_fn(engine, ctx, domain, plan)
            else:
                domains.generic_apply(engine, ctx, domain, plan)
        except clientlib.ApiError as e:
            rep.fail(f"{domain.name}: {e}")
        for action in plan.actions:
            if action.kind != planner.NOOP:
                print(f"  {action.line()}")
    lock.save()
    rep.summary()
    rep.write_json()
    if getattr(args, "html", None):
        path = htmlreport.write(rep, args.html, title=f"ascsync {command}")
        print(f"Report: {paths.rel_to_asc(path)}")
    return rep.exit_code()


def cmd_plan(args) -> int:
    return _run_apply(args, dry_run=True, command="plan")


def cmd_push(args) -> int:
    if not args.yes:
        print("Dry run (no --yes) — nothing will be written.\n")
    return _run_apply(args, dry_run=not args.yes, command="push")


def cmd_validate(args) -> int:
    locales = paths.load_locales()
    problems = []
    print("Offline validation (no API access)"
          + (" — including submission readiness" if args.readiness else ""))
    for domain in select(args.domain):
        found = validate.validate_domain(domain, locales,
                                         check_assets=not args.skip_assets,
                                         readiness=args.readiness)
        if found:
            print(f"\n== {domain.title or domain.name} ==")
            for p in found:
                print(f"  - {p}")
        problems.extend(found)
    language_problems = validate.validate_app_languages(locales)
    if language_problems:
        print("\n== Languages ==")
        for p in language_problems:
            print(f"  - {p}")
        problems.extend(language_problems)

    ids = {}
    for domain, resource in (("achievements", game_center.ACHIEVEMENTS),
                             ("leaderboards", game_center.LEADERBOARDS),
                             ("iap", iap_res.PRODUCTS)):
        target = next((d for d in ALL_DOMAINS if d.resource is resource), None)
        doc = domains.load_doc(target) if target else None
        ids[domain] = [str(i.get(resource.key))
                       for i in domains.doc_items(doc, resource)] if doc else []
    drift = validate.validate_code_drift(ids)
    if drift:
        print("\n== Cross-check against the app source ==")
        for p in drift:
            print(f"  - {p}")
        problems.extend(drift)

    event_doc = domains.load_doc(events_res.EVENTS_DOMAIN)
    if event_doc:
        event_problems = validate.validate_events(
            domains.doc_items(event_doc, events_res.EVENTS))
        if event_problems:
            print("\n== Event deadlines and quotas ==")
            for p in event_problems:
                print(f"  - {p}")
            problems.extend(event_problems)

    if args.readiness:
        structural = validate.validate_readiness(locales)
        if structural:
            print("\n== Submission readiness ==")
            for p in structural:
                print(f"  - {p}")
            problems.extend(structural)
        print("\n== Not checkable offline — tick these off yourself ==")
        for note in validate.NOT_CHECKABLE:
            print(f"  . {note}")

    print(f"\n{len(problems)} finding(s)." if problems else "\nAll good.")
    if not args.readiness:
        print("Tip: 'ascsync validate --readiness' also checks what submission "
              "needs (support URL, categories, review contact, ...).")
    return 1 if problems else 0


def cmd_events_generate(args) -> int:
    ahead = parse_ahead(args.ahead)
    territories = None
    pricing_doc = paths.data_path("pricing.json")
    if os.path.exists(pricing_doc):
        data = paths.read_json(pricing_doc)
        items = data.get("items")
        entry = items[0] if isinstance(items, list) and items else (
            items if isinstance(items, dict) else {})
        available = [t.get("territory") for t in (entry.get("territoryAvailabilities") or [])
                     if t.get("available") and t.get("territory")]
        territories = available or None
    generated, messages = leaderboard_events.generate(ahead_days=ahead,
                                                      territories=territories)
    for message in messages:
        print(f"  {message}")
    if any(m.startswith("[stop]") for m in messages):
        print("Aborted — nothing written.")
        return 1
    path = leaderboard_events.write(generated)
    print(f"{len(generated)} Event(s) -> {paths.rel_to_asc(path)}")
    print("Next: 'ascsync validate --domain events' (checks texts and images), "
          "then 'ascsync push --domain events --yes'.")
    return 0


def parse_ahead(value: str) -> int:
    """'12w' / '84d' / '84' -> days."""
    value = (value or "12w").strip().lower()
    if value.endswith("w"):
        return int(value[:-1]) * 7
    if value.endswith("d"):
        return int(value[:-1])
    return int(value)


def cmd_achievements_template(args) -> int:
    from ascsync.generators import achievement_template
    for message in achievement_template.run(force=args.force):
        print(f"  {message}")
    print("Next: fill in the texts in data/gamecenter/achievements.json, put the "
          "icons into assets/gamecenter/achievements/, then run 'ascsync validate'.")
    return 0


def cmd_privacy_publish(args) -> int:
    rep = report.Report("privacy publish", dry_run=not args.yes)
    ctx = build_context(dry_run=not args.yes, args=args)
    plan = rep.plan_for("privacy")
    privacy_res.publish(ctx, plan)
    for action in plan.actions:
        print(f"  {action.line()}")
    rep.summary()
    return rep.exit_code()


def cmd_submit(args) -> int:
    rep = report.Report("submit", dry_run=not args.yes)
    ctx = build_context(dry_run=not args.yes, args=args)
    plan = rep.plan_for("submission")
    submission_id = submission.ensure_submission(ctx, plan)
    if args.version:
        version = app_store.resolve_target_version(ctx)
        if not version:
            rep.fail(f"Version {args.version} not found, or not submittable.")
            rep.summary()
            return 1
        submission.add_item(ctx, plan, submission_id, "appStoreVersion",
                            "appStoreVersions", version["id"], args.version)
    for reference in args.event or []:
        match = [e for e in ctx.client.get_all(f"/v1/apps/{ctx.app_id}/appEvents")
                 if (e.get("attributes", {}) or {}).get("referenceName") == reference]
        if not match:
            rep.fail(f"Event '{reference}' not found in ASC.")
            continue
        submission.add_item(ctx, plan, submission_id, "appEvent", "appEvents",
                            match[0]["id"], reference)
    if args.send:
        submission.submit(ctx, plan, submission_id)
    else:
        print("  Note: without --send the submission is only assembled, not sent.")
    for action in plan.actions:
        print(f"  {action.line()}")
    rep.summary()
    return rep.exit_code()


def cmd_releases(args) -> int:
    rep = report.Report("releases", dry_run=not args.yes)
    ctx = build_context(dry_run=not args.yes, args=args)
    lock = assetlib.AssetLock()
    engine = make_engine(ctx, args, lock)
    if getattr(args, "version", None):
        print("  [info] --version is ignored: Game Center releases hang off the "
              "gameCenterDetail, not off an app version.")
    for kind in args.kind or ("achievements", "leaderboards"):
        plan = rep.plan_for(kind)
        print(f"\n== Releases {kind} ==")
        game_center.sync_releases(engine, ctx, plan, kind)
        for action in plan.actions:
            if action.kind != planner.NOOP:
                print(f"  {action.line()}")
    rep.summary()
    return rep.exit_code()


# ---------------------------------------------------------------------------
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="ascsync", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--verbose", action="store_true", help="show every HTTP call")
    sub = parser.add_subparsers(dest="command", required=True)

    def add_domain_args(p, with_assets=True):
        p.add_argument("--domain", action="append", default=[],
                       help="store|gamecenter|iap|events|pricing|privacy|pages "
                            "(repeatable; default: all)")
        p.add_argument("--only", action="append", default=[],
                       help="only these keys (vendorIdentifier/productId/…)")
        p.add_argument("--only-locale", action="append", default=[])
        p.add_argument("--version", help="versionString of the target version")
        if with_assets:
            p.add_argument("--skip-assets", action="store_true",
                           help="skip images and videos")

    p_init = sub.add_parser("init", help="create a project here and fill it")
    p_init.add_argument("--bundle-id", help="the app's bundle id")
    p_init.add_argument("--locales", default="en-US",
                        help="comma separated, first one is primary (default: en-US)")
    p_init.add_argument("--no-pull", action="store_true",
                        help="scaffold only, do not contact ASC")
    p_init.add_argument("--force", action="store_true",
                        help="overwrite an existing data/app.json")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="check access and states")
    p_doctor.set_defaults(func=cmd_doctor)

    p_pull = sub.add_parser("pull", help="ASC state into data/ and .snapshot/")
    add_domain_args(p_pull)
    p_pull.add_argument("--all", action="store_true", help="(default) all domains")
    p_pull.add_argument("--snapshot-only", action="store_true",
                        help="write .snapshot/ only, leave data/ untouched "
                             "(merge afterwards via 'plan')")
    p_pull.set_defaults(func=cmd_pull)

    p_plan = sub.add_parser("plan", help="three-way diff, writes nothing")
    p_plan.add_argument("--html", metavar="FILE",
                        help="also write a readable report (images included)")
    add_domain_args(p_plan)
    p_plan.set_defaults(func=cmd_plan)

    p_push = sub.add_parser("push", help="write (without --yes: dry run)")
    add_domain_args(p_push)
    p_push.add_argument("--yes", action="store_true", help="actually write")
    p_push.add_argument("--allow-pricing", action="store_true",
                        help="include availability and prices")
    p_push.add_argument("--allow-pages", action="store_true",
                        help="include custom product pages")
    p_push.add_argument("--html", metavar="FILE",
                        help="also write a readable report (images included)")
    p_push.set_defaults(func=cmd_push)

    p_validate = sub.add_parser("validate", help="check offline")
    add_domain_args(p_validate)
    p_validate.add_argument("--readiness", action="store_true",
                            help="also check what SUBMISSION needs: fields the "
                                 "API leaves optional (support URL, copyright, "
                                 "review contact), categories, screenshots per "
                                 "display type, app privacy, snapshot, event dates")
    p_validate.set_defaults(func=cmd_validate)

    p_events = sub.add_parser("events", help="event tools")
    events_sub = p_events.add_subparsers(dest="events_command", required=True)
    p_generate = events_sub.add_parser("generate", help="generate occurrence drafts")
    p_generate.add_argument("--ahead", default="12w", help="lead time, e.g. 12w or 84d")
    p_generate.set_defaults(func=cmd_events_generate)

    p_achievements = sub.add_parser("achievements", help="achievement tools")
    achievements_sub = p_achievements.add_subparsers(dest="achievements_command",
                                                     required=True)
    p_template = achievements_sub.add_parser(
        "template", help="add ids found in the source (existing texts are kept)")
    p_template.add_argument("--force", action="store_true",
                            help="regenerate everything — existing texts are lost")
    p_template.set_defaults(func=cmd_achievements_template)

    p_privacy = sub.add_parser("privacy", help="app privacy")
    privacy_sub = p_privacy.add_subparsers(dest="privacy_command", required=True)
    p_publish = privacy_sub.add_parser("publish", help="publish the data usage")
    p_publish.add_argument("--yes", action="store_true")
    p_publish.set_defaults(func=cmd_privacy_publish)

    p_submit = sub.add_parser("submit", help="submit a version or event for review")
    p_submit.add_argument("--version")
    p_submit.add_argument("--event", action="append", default=[])
    p_submit.add_argument("--send", action="store_true",
                          help="actually send the submission")
    p_submit.add_argument("--yes", action="store_true")
    p_submit.set_defaults(func=cmd_submit)

    p_releases = sub.add_parser("releases",
                                help="release achievements and leaderboards")
    p_releases.add_argument("--version")
    p_releases.add_argument("--kind", action="append",
                            choices=sorted(game_center.RELEASE_KINDS))
    p_releases.add_argument("--yes", action="store_true")
    p_releases.set_defaults(func=cmd_releases)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
