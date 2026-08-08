"""In-app events.

Apple's constraints (as of July 2026):
  reference name 64 / name 30 / short description 50 / long description 120
  exactly one badge · duration 15 min - 31 days · published at most 14 days
  before the start · at most 10 published at once · at most 15 approved in ASC
  event card 16:9 (1920x1080-3840x2160), detail page 9:16 (1080x1920-2160x3840)

Assets are NOT stored per occurrence but per variant (metric + play mode).
The resolution order is declared in CARD and DETAIL.
"""
from __future__ import annotations

from ..core.registry import (AssetSpec, Domain, Enum, Field, ImageRule, Limit,
                             RequireTogether, Resource)

BADGES = ("LIVE_EVENT", "PREMIERE", "CHALLENGE", "COMPETITION",
          "NEW_SEASON", "MAJOR_UPDATE", "SPECIAL_EVENT")

CARD_RULE = ImageRule(min_width=1920, min_height=1080, max_width=3840,
                      max_height=2160, aspect=16 / 9.0, allow_alpha=False)
DETAIL_RULE = ImageRule(min_width=1080, min_height=1920, max_width=2160,
                        max_height=3840, aspect=9 / 16.0, allow_alpha=False)

# Aufloesungs-Reihenfolge: <metrik>-<spielart> -> <metrik> -> default
_CARD_PATHS = ("events/{assetVariant}/{locale}/card.png",
               "events/{metric}/{locale}/card.png",
               "events/default/{locale}/card.png")
_DETAIL_PATHS = ("events/{assetVariant}/{locale}/detail.png",
                 "events/{metric}/{locale}/detail.png",
                 "events/default/{locale}/detail.png")

CARD = AssetSpec(
    name="card",
    api_type="appEventScreenshots",
    parent_rel="appEventLocalization",
    parent_type="appEventLocalizations",
    relationship="appEventScreenshots",
    path=_CARD_PATHS[0],
    fallbacks=_CARD_PATHS[1:],
    # appEventScreenshots does not accept sourceFileChecksum on the closing
    # PATCH (409, "is not an attribute") — unlike appScreenshots. A change is
    # detected through assets.lock.json instead.
    checksum=False,
    type_attr="appEventAssetType",
    type_value="EVENT_CARD",
    video_api_type="appEventVideoClips",
    rule=CARD_RULE,
)

DETAIL = AssetSpec(
    name="detail",
    api_type="appEventScreenshots",
    parent_rel="appEventLocalization",
    parent_type="appEventLocalizations",
    relationship="appEventScreenshots",
    path=_DETAIL_PATHS[0],
    fallbacks=_DETAIL_PATHS[1:],
    # appEventScreenshots does not accept sourceFileChecksum on the closing
    # PATCH (409, "is not an attribute") — unlike appScreenshots. A change is
    # detected through assets.lock.json instead.
    checksum=False,
    type_attr="appEventAssetType",
    type_value="EVENT_DETAILS_PAGE",
    video_api_type="appEventVideoClips",
    rule=DETAIL_RULE,
)

EVENT_LOCALIZATIONS = Resource(
    type="appEventLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    # The path is /v1/appEvents/<id>/localizations, not /appEventLocalizations.
    # Without this the push never found an existing localization and tried to
    # create it every time — the API then answers "already exists".
    list_rel="localizations",
    parent_rel="appEvent",
    parent_type="appEvents",
    writable={
        "name": Limit(30),
        "shortDescription": Limit(50),
        "longDescription": Limit(120),
    },
    assets=(CARD, DETAIL),
    quirks=(RequireTogether(("name", "shortDescription", "longDescription")),),
)

EVENTS = Resource(
    type="appEvents",
    key="referenceName",
    root_path="/v1/apps/{app_id}/appEvents",
    parent_rel="app",
    parent_type="apps",
    writable={
        "referenceName": Limit(64),
        "badge": Enum(*BADGES),
        "deepLink": Field(kind="url"),
        # "NO_COST_ASSOCIATED", not "NO_COST" — that is also what the draft
        # ASC created itself used. The short name is rejected with a 400.
        "purchaseRequirement": Enum("NO_COST_ASSOCIATED", "IN_APP_PURCHASE",
                                    "SUBSCRIPTION", "IN_APP_PURCHASE_OR_SUBSCRIPTION"),
        "primaryLocale": Field(),
        "priority": Enum("HIGH", "NORMAL"),
        "purpose": Enum("APPROPRIATE_FOR_ALL_USERS", "ATTRACT_NEW_USERS",
                        "KEEP_ACTIVE_USERS_INFORMED", "BRING_BACK_LAPSED_USERS"),
        "territorySchedules": Field(kind="list"),
    },
    readonly=("eventState", "archivedTerritorySchedules"),
    children=(EVENT_LOCALIZATIONS,),
    # Once approved only dates, territories and priority can change; once
    # started only the end date — hence the gate.
    editable_states=frozenset({"DRAFT", "READY_FOR_REVIEW", "REJECTED",
                               "WAITING_FOR_REVIEW", "IN_REVIEW",
                               "ACCEPTED", "APPROVED"}),
)

EVENTS_DOMAIN = Domain(
    name="events", group="events", data_file="events/events.json",
    resource=EVENTS, title="In-app events",
    notes="Drafts come from 'ascsync events generate'; submitting stays manual.",
)

DOMAINS = (EVENTS_DOMAIN,)

# Apple's quotas — the generator watches these.
MAX_APPROVED = 15
MAX_PUBLISHED = 10
MAX_OVERLAPPING = 10
MAX_DURATION_DAYS = 31
MAX_PUBLISH_LEAD_DAYS = 14
