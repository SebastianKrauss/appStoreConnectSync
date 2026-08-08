"""Game Center: achievements, leaderboards, leaderboard sets, releases.

The parent is the app's `gameCenterDetail` (or a `gameCenterGroup` when
data/app.json names a `gameCenterGroupId`). It is resolved once and cached in
the context.
"""
from __future__ import annotations

from typing import List, Optional, Tuple

from ..core import domains, planner
from ..core.registry import (AssetSpec, Bool, Date, Domain, Enum, Field,
                             ImageRule, Int, Limit, RequireTogether, Resource,
                             Url)

ICON_RULE = ImageRule(exact=((512, 512), (1024, 1024)), allow_alpha=False)

# ---------------------------------------------------------------------------
# Achievements
# ---------------------------------------------------------------------------
ACHIEVEMENT_IMAGE = AssetSpec(
    name="image",
    api_type="gameCenterAchievementImages",
    parent_rel="gameCenterAchievementLocalization",
    parent_type="gameCenterAchievementLocalizations",
    relationship="gameCenterAchievementImage",
    path="gamecenter/achievements/{parent_slug}.png",
    rule=ICON_RULE,
)

ACHIEVEMENT_LOCALIZATION = Resource(
    type="gameCenterAchievementLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    list_rel="localizations",
    parent_rel="gameCenterAchievement",
    parent_type="gameCenterAchievements",
    writable={
        "name": Limit(100),
        "beforeEarnedDescription": Limit(200),
        "afterEarnedDescription": Limit(200),
    },
    assets=(ACHIEVEMENT_IMAGE,),
    quirks=(RequireTogether(("name", "beforeEarnedDescription",
                             "afterEarnedDescription")),),
)

ACHIEVEMENTS = Resource(
    type="gameCenterAchievements",
    key="vendorIdentifier",
    writable={
        "referenceName": Limit(64),
        "vendorIdentifier": Field(immutable=True),
        "points": Int(required=True),
        "showBeforeEarned": Bool(),
        "repeatable": Bool(),
    },
    readonly=("archived",),
    children=(ACHIEVEMENT_LOCALIZATION,),
)

# ---------------------------------------------------------------------------
# Bestenlisten
# ---------------------------------------------------------------------------
LEADERBOARD_IMAGE = AssetSpec(
    name="image",
    api_type="gameCenterLeaderboardImages",
    parent_rel="gameCenterLeaderboardLocalization",
    parent_type="gameCenterLeaderboardLocalizations",
    relationship="gameCenterLeaderboardImage",
    path="gamecenter/leaderboards/{parent_slug}.png",
    rule=ICON_RULE,
)

LEADERBOARD_LOCALIZATION = Resource(
    type="gameCenterLeaderboardLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    list_rel="localizations",
    parent_rel="gameCenterLeaderboard",
    parent_type="gameCenterLeaderboards",
    writable={
        "name": Limit(100),
        "formatterOverride": Field(),
        "formatterSuffix": Limit(20),
        "formatterSuffixSingular": Limit(20),
    },
    assets=(LEADERBOARD_IMAGE,),
    quirks=(RequireTogether(("name",)),),
)

LEADERBOARDS = Resource(
    type="gameCenterLeaderboards",
    key="vendorIdentifier",
    writable={
        "referenceName": Limit(64),
        "vendorIdentifier": Field(immutable=True),
        "defaultFormatter": Enum(
            "INTEGER", "DECIMAL_POINT_1_PLACE", "DECIMAL_POINT_2_PLACE",
            "DECIMAL_POINT_3_PLACE", "ELAPSED_TIME_MILLISECOND",
            "ELAPSED_TIME_SECOND", "ELAPSED_TIME_MINUTE", "MONEY_POUND",
            "MONEY_DOLLAR", "MONEY_EURO", "MONEY_FRANC", "MONEY_KRONER",
            "MONEY_YEN", "MONEY_RUPEE", "MONEY_WON"),
        "submissionType": Enum("BEST_SCORE", "MOST_RECENT_SCORE"),
        "scoreSortType": Enum("ASC", "DESC"),
        "scoreRangeStart": Field(kind="int"),
        "scoreRangeEnd": Field(kind="int"),
        "recurrenceStartDate": Date(),
        "recurrenceDuration": Field(),      # ISO-8601, z. B. P7D
        "recurrenceRule": Field(),          # RRULE, z. B. FREQ=DAILY;INTERVAL=21
    },
    readonly=("archived", "activityProperties"),
    children=(LEADERBOARD_LOCALIZATION,),
)

# ---------------------------------------------------------------------------
# Bestenlisten-Gruppen (Sets)
# ---------------------------------------------------------------------------
LEADERBOARD_SET_LOCALIZATION = Resource(
    type="gameCenterLeaderboardSetLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    list_rel="localizations",
    parent_rel="gameCenterLeaderboardSet",
    parent_type="gameCenterLeaderboardSets",
    writable={"name": Limit(100)},
    quirks=(RequireTogether(("name",)),),
)

LEADERBOARD_SETS = Resource(
    type="gameCenterLeaderboardSets",
    key="vendorIdentifier",
    writable={
        "referenceName": Limit(64),
        "vendorIdentifier": Field(immutable=True),
    },
    readonly=("archived",),
    children=(LEADERBOARD_SET_LOCALIZATION,),
)


# ---------------------------------------------------------------------------
# Parent aufloesen + Treiber
# ---------------------------------------------------------------------------
def resolve_parent(ctx: domains.Context) -> Tuple[str, str]:
    """(parent_type, parent_id) — gameCenterDetails or gameCenterGroups."""
    cached = ctx.cache.get("gcParent")
    if cached:
        return cached
    group_id = (ctx.app.get("gameCenterGroupId") or "").strip()
    if group_id:
        parent = ("gameCenterGroups", group_id)
    else:
        detail = ctx.client.get_optional(f"/v1/apps/{ctx.app_id}/gameCenterDetail")
        if not detail:
            raise SystemExit("the app has no gameCenterDetail — enable Game "
                             "Center for it in ASC.")
        parent = ("gameCenterDetails", detail["id"])
    ctx.cache["gcParent"] = parent
    return parent


def _pull(engine, ctx: domains.Context, domain: Domain) -> List[dict]:
    parent_type, parent_id = resolve_parent(ctx)
    return engine.fetch(domain.resource, parent_type=parent_type, parent_id=parent_id)


def _apply(engine, ctx: domains.Context, domain: Domain, plan: planner.Plan) -> None:
    parent_type, parent_id = resolve_parent(ctx)
    desired = domains.doc_items(domains.load_doc(domain), domain.resource)
    snapshot_doc = domains.load_doc(domain, snapshot=True)
    snapshot = (domains.doc_items(snapshot_doc, domain.resource)
                if snapshot_doc else None)
    if snapshot is None:
        plan.add(planner.SKIP, domain.name,
                 "no snapshot — run 'ascsync pull' before the first push")
    remote = engine.fetch(domain.resource, parent_type=parent_type, parent_id=parent_id)
    if domain.resource is ACHIEVEMENTS and len(desired) > 100:
        plan.add(planner.SKIP, domain.name,
                 f"{len(desired)} Achievements — Game Center deckelt bei 100")
    engine.sync(domain.resource, desired, snapshot, remote, plan,
                parent_id=parent_id, parent_type=parent_type)


ACHIEVEMENTS_DOMAIN = Domain(
    name="achievements", group="gamecenter",
    data_file="gamecenter/achievements.json",
    resource=ACHIEVEMENTS, title="Game-Center-Achievements",
    pull_fn=_pull, apply_fn=_apply,
)

LEADERBOARDS_DOMAIN = Domain(
    name="leaderboards", group="gamecenter",
    data_file="gamecenter/leaderboards.json",
    resource=LEADERBOARDS, title="Game-Center-Bestenlisten",
    pull_fn=_pull, apply_fn=_apply,
)

LEADERBOARD_SETS_DOMAIN = Domain(
    name="leaderboard_sets", group="gamecenter",
    data_file="gamecenter/leaderboard_sets.json",
    resource=LEADERBOARD_SETS, title="Bestenlisten-Gruppen",
    pull_fn=_pull, apply_fn=_apply,
)

DOMAINS = (ACHIEVEMENTS_DOMAIN, LEADERBOARDS_DOMAIN, LEADERBOARD_SETS_DOMAIN)


# ---------------------------------------------------------------------------
# Releases: which achievement and which leaderboard become visible
# ---------------------------------------------------------------------------
#   (release resource, relationship to the record, type of the record,
#    relationship on the gameCenterDetail, resource declaration)
RELEASE_KINDS = {
    "achievements": ("gameCenterAchievementReleases", "gameCenterAchievement",
                     "gameCenterAchievements", "achievementReleases", ACHIEVEMENTS),
    "leaderboards": ("gameCenterLeaderboardReleases", "gameCenterLeaderboard",
                     "gameCenterLeaderboards", "leaderboardReleases", LEADERBOARDS),
    "leaderboard_sets": ("gameCenterLeaderboardSetReleases", "gameCenterLeaderboardSet",
                         "gameCenterLeaderboardSets", "leaderboardSetReleases",
                         LEADERBOARD_SETS),
}


def sync_releases(engine, ctx: domains.Context, plan: planner.Plan, kind: str) -> None:
    """Create the missing release records.

    Without a release record a new achievement or leaderboard stays invisible
    after publication — exactly the trap people miss on a first submission.

    Important: a release hangs off the **gameCenterDetail**, not off an app
    version. The resource knows exactly two relationships, `gameCenterDetail`
    and the record itself; sending `version` along earns a 409 ("'version' is
    not a relationship"). The record therefore applies to the app, not to a
    single version.
    """
    release_type, item_rel, item_type, detail_rel, resource = RELEASE_KINDS[kind]
    parent_type, parent_id = resolve_parent(ctx)
    items = engine.fetch(resource, parent_type=parent_type, parent_id=parent_id)

    # One call instead of one query per record: which records already have a
    # release? The mapping is in the included objects.
    released_ids = set()
    listing = ctx.client.get(f"/v1/{parent_type}/{parent_id}/{detail_rel}",
                             **{"include": item_rel, "limit": 200})
    for rel in listing.get("data", []):
        target = ((rel.get("relationships") or {}).get(item_rel) or {}).get("data") or {}
        if target.get("id"):
            released_ids.add(target["id"])
    for inc in listing.get("included", []):
        released_ids.add(inc["id"])

    for item in items:
        key = str(item.get(resource.key))
        item_id = item["readonly"]["id"]
        path = f"releases/{key}"
        if item_id in released_ids:
            plan.add(planner.NOOP, path, "bereits freigegeben")
            continue
        plan.add(planner.CREATE, path, f"{release_type} anlegen",
                 executed=not ctx.client.dry_run)
        if ctx.client.dry_run:
            continue
        # gameCenterDetails -> "gameCenterDetail". The shape is unverified for
        # a Game Center group as the parent.
        parent_rel = parent_type[:-1]
        ctx.client.post(f"/v1/{release_type}", {
            "data": {
                "type": release_type,
                "relationships": {
                    item_rel: {"data": {"type": item_type, "id": item_id}},
                    parent_rel: {"data": {"type": parent_type, "id": parent_id}},
                },
            }
        })
