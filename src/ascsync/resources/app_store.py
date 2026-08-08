"""The App Store page: app info (name, subtitle, categories) and versions,
including texts, screenshots, preview videos, review details and custom
product pages.
"""
from __future__ import annotations

from typing import List, Optional

from ..core import domains, planner
from ..core.registry import (AssetSpec, Bool, Date, Domain, Enum, Field,
                             ImageRule, Limit, Resource, SkipFieldOnError, Url)

PLATFORM = "IOS"

# ASC allows metadata changes only while the version is in one of these states.
EDITABLE_STATES = frozenset({"PREPARE_FOR_SUBMISSION", "METADATA_REJECTED",
                             "DEVELOPER_REJECTED", "REJECTED", "INVALID_BINARY",
                             "READY_FOR_REVIEW", "WAITING_FOR_EXPORT_COMPLIANCE"})

# Display type (the directory name under assets/screenshots/<locale>/) ->
# expected sizes. Apple files 6.9" captures under the 6.7" class and iPad 13"
# under the 12.9" one. A new class means a new directory plus an entry here
# (used for validation only).
SCREENSHOT_SIZES = {
    "APP_IPHONE_67": ((1290, 2796), (2796, 1290), (1320, 2868), (2868, 1320)),
    "APP_IPHONE_65": ((1242, 2688), (2688, 1242), (1284, 2778), (2778, 1284)),
    "APP_IPAD_PRO_3GEN_129": ((2048, 2732), (2732, 2048), (2064, 2752), (2752, 2064)),
    "APP_IPAD_PRO_129": ((2048, 2732), (2732, 2048)),
}

SCREENSHOT_RULE = ImageRule(min_width=640, min_height=640, allow_alpha=False)

# Without these display types ASC will not accept the submission. The iPad
# class belongs here only if the app is universal — remove it for an
# iPhone-only app. Checked by `ascsync validate --readiness`.
REQUIRED_DISPLAY_TYPES = ("APP_IPHONE_67", "APP_IPAD_PRO_3GEN_129")
PREVIEW_RULE = ImageRule(formats=(".mp4", ".mov", ".m4v"))

SCREENSHOTS = AssetSpec(
    name="screenshots",
    api_type="appScreenshots",
    parent_rel="appScreenshotSet",
    parent_type="appStoreVersionLocalizations",
    relationship="appScreenshots",
    path="screenshots/{locale}",
    single=False,
    checksum=True,
    rule=SCREENSHOT_RULE,
    set_api_type="appScreenshotSets",
    set_key_attr="screenshotDisplayType",
    set_parent_rel="appStoreVersionLocalization",
    set_relationship="appScreenshotSets",
)

PREVIEWS = AssetSpec(
    name="previews",
    api_type="appPreviews",
    parent_rel="appPreviewSet",
    parent_type="appStoreVersionLocalizations",
    relationship="appPreviews",
    path="previews/{locale}",
    single=False,
    checksum=True,
    rule=PREVIEW_RULE,
    set_api_type="appPreviewSets",
    set_key_attr="previewType",
    set_parent_rel="appStoreVersionLocalization",
    set_relationship="appPreviewSets",
)

# ---------------------------------------------------------------------------
# App info: name, subtitle, privacy details
# ---------------------------------------------------------------------------
APP_INFO_LOCALIZATIONS = Resource(
    type="appInfoLocalizations",
    key="locale",
    keyed=True,
    parent_rel="appInfo",
    parent_type="appInfos",
    writable={
        "name": Limit(30, submission=True),
        "subtitle": Limit(30),
        "privacyPolicyUrl": Url(submission=True),
        "privacyChoicesUrl": Url(),
        "privacyPolicyText": Limit(4000),
    },
)

# ---------------------------------------------------------------------------
# Versions: texts, screenshots, previews, review details
# ---------------------------------------------------------------------------
VERSION_LOCALIZATIONS = Resource(
    type="appStoreVersionLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    parent_rel="appStoreVersion",
    parent_type="appStoreVersions",
    writable={
        "description": Limit(4000, submission=True),
        "keywords": Limit(100, submission=True),
        "promotionalText": Limit(170),
        "whatsNew": Limit(4000),
        "marketingUrl": Url(),
        "supportUrl": Url(submission=True),
    },
    assets=(SCREENSHOTS, PREVIEWS),
    quirks=(SkipFieldOnError("whatsNew",
                             "Apple rejects 'What's New' on a very first "
                             "version — field skipped"),),
)

REVIEW_DETAIL = Resource(
    type="appStoreReviewDetails",
    key="id",
    doc_field="reviewDetail",
    singleton=True,
    list_rel="appStoreReviewDetail",
    parent_rel="appStoreVersion",
    parent_type="appStoreVersions",
    writable={
        "contactFirstName": Limit(50, submission=True),
        "contactLastName": Limit(50, submission=True),
        "contactPhone": Limit(50, submission=True),
        "contactEmail": Limit(100, submission=True),
        "demoAccountName": Limit(100),
        "demoAccountPassword": Limit(100),
        "demoAccountRequired": Bool(),
        "notes": Limit(4000),
    },
)

AGE_RATING = Resource(
    type="ageRatingDeclarations",
    key="id",
    doc_field="ageRating",
    singleton=True,
    list_rel="ageRatingDeclaration",
    parent_rel="ageRatingDeclaration",
    parent_type="appStoreVersions",
    creatable=False,     # always exists; only ever patched
    writable={
        "alcoholTobaccoOrDrugUseOrReferences": Field(),
        "contests": Field(),
        "gamblingSimulated": Field(),
        "horrorOrFearThemes": Field(),
        "matureOrSuggestiveThemes": Field(),
        "medicalOrTreatmentInformation": Field(),
        "profanityOrCrudeHumor": Field(),
        "sexualContentGraphicAndNudity": Field(),
        "sexualContentOrNudity": Field(),
        "violenceCartoonOrFantasy": Field(),
        "violenceRealistic": Field(),
        "violenceRealisticProlongedGraphicOrSadistic": Field(),
        "gambling": Bool(),
        "unrestrictedWebAccess": Bool(),
        "kidsAgeBand": Field(),
        "ageRatingOverride": Field(),
    },
)

VERSIONS = Resource(
    type="appStoreVersions",
    key="versionString",
    root_path="/v1/apps/{app_id}/appStoreVersions",
    parent_rel="app",
    parent_type="apps",
    writable={
        "versionString": Field(immutable=True),
        "platform": Field(immutable=True),
        "copyright": Limit(200, submission=True),
        "releaseType": Enum("MANUAL", "AFTER_APPROVAL", "SCHEDULED"),
        "earliestReleaseDate": Date(),
    },
    readonly=("appStoreState", "appVersionState", "createdDate"),
    children=(VERSION_LOCALIZATIONS, REVIEW_DETAIL, AGE_RATING),
    editable_states=EDITABLE_STATES,
)


# ---------------------------------------------------------------------------
# App info driver (the parent has to be resolved first)
# ---------------------------------------------------------------------------
def resolve_app_info_id(ctx: domains.Context) -> Optional[str]:
    cached = ctx.cache.get("appInfoId")
    if cached:
        return cached
    for info in ctx.client.get_all(f"/v1/apps/{ctx.app_id}/appInfos"):
        state = (info.get("attributes", {}) or {}).get("state") or \
                (info.get("attributes", {}) or {}).get("appStoreState") or ""
        if state in EDITABLE_STATES:
            ctx.cache["appInfoId"] = info["id"]
            return info["id"]
    return None


def _pull_app_info(engine, ctx: domains.Context, domain: Domain) -> List[dict]:
    info_id = resolve_app_info_id(ctx)
    if not info_id:
        return []
    return engine.fetch(APP_INFO_LOCALIZATIONS, parent_type="appInfos", parent_id=info_id)


def _apply_app_info(engine, ctx: domains.Context, domain: Domain,
                    plan: planner.Plan) -> None:
    info_id = resolve_app_info_id(ctx)
    if not info_id:
        plan.add(planner.BLOCKED, "appInfo",
                 "no editable app info — ASC needs a version in state "
                 "'Prepare for Submission' for that")
        return
    desired = domains.doc_items(domains.load_doc(domain), domain.resource)
    snapshot_doc = domains.load_doc(domain, snapshot=True)
    snapshot = (domains.doc_items(snapshot_doc, domain.resource)
                if snapshot_doc else None)
    remote = engine.fetch(APP_INFO_LOCALIZATIONS, parent_type="appInfos",
                          parent_id=info_id)
    engine.sync(APP_INFO_LOCALIZATIONS, desired, snapshot, remote, plan,
                parent_id=info_id, parent_type="appInfos")
    sync_categories(ctx, plan, info_id)


CATEGORY_FIELDS = ("primaryCategory", "primarySubcategoryOne", "primarySubcategoryTwo",
                   "secondaryCategory", "secondarySubcategoryOne",
                   "secondarySubcategoryTwo")


def sync_categories(ctx: domains.Context, plan: planner.Plan, info_id: str) -> None:
    """Categories are relationships, not attributes — hence their own path.

    Source: data/app.json -> "categories". Empty values are ignored.
    """
    wanted = {k: v for k, v in (ctx.app.get("categories") or {}).items()
              if k in CATEGORY_FIELDS and v}
    if not wanted:
        return
    # Without 'include' the API returns only a link per relationship and no
    # "data" — which made the remote state look permanently empty, so the
    # categories were rewritten on every single run.
    info = ctx.client.get(f"/v1/appInfos/{info_id}", **{"include": ",".join(wanted)})
    relationships = (info.get("data", {}) or {}).get("relationships", {}) or {}
    changed = {}
    for field_name, value in wanted.items():
        current = ((relationships.get(field_name) or {}).get("data") or {}).get("id")
        if current != value:
            changed[field_name] = value
    if not changed:
        plan.add(planner.NOOP, "appInfo/categories")
        return
    plan.add(planner.UPDATE, "appInfo/categories", fields=changed,
             executed=not ctx.client.dry_run)
    ctx.client.patch(f"/v1/appInfos/{info_id}", {
        "data": {
            "type": "appInfos", "id": info_id,
            "relationships": {k: {"data": {"type": "appCategories", "id": v}}
                              for k, v in changed.items()},
        }
    })


# ---------------------------------------------------------------------------
# Versions driver
# ---------------------------------------------------------------------------
def _pull_versions(engine, ctx: domains.Context, domain: Domain) -> List[dict]:
    return engine.fetch(VERSIONS, app_id=ctx.app_id)


def _apply_versions(engine, ctx: domains.Context, domain: Domain,
                    plan: planner.Plan) -> None:
    desired = domains.doc_items(domains.load_doc(domain), domain.resource)
    if ctx.version:
        desired = [v for v in desired if v.get("versionString") == ctx.version]
        if not desired:
            plan.add(planner.SKIP, "appStoreVersions",
                     f"version {ctx.version} is not in data/{domain.data_file}")
            return
    snapshot_doc = domains.load_doc(domain, snapshot=True)
    snapshot = (domains.doc_items(snapshot_doc, domain.resource)
                if snapshot_doc else None)
    remote = engine.fetch(VERSIONS, app_id=ctx.app_id)
    remote_keys = {str(v.get("versionString")) for v in remote}
    for item in desired:
        if str(item.get("versionString")) not in remote_keys:
            plan.add(planner.SKIP, str(item.get("versionString")),
                     "version does not exist in ASC — it will be created, "
                     "provided no other version is open")
    engine.sync(VERSIONS, desired, snapshot, remote, plan)


def resolve_target_version(ctx: domains.Context) -> Optional[dict]:
    """The version that review and submission refer to."""
    versions = ctx.client.get_all(f"/v1/apps/{ctx.app_id}/appStoreVersions",
                                  **{"filter[platform]": PLATFORM})
    if ctx.version:
        for v in versions:
            if (v.get("attributes", {}) or {}).get("versionString") == ctx.version:
                return v
        return None
    for v in versions:
        attributes = v.get("attributes", {}) or {}
        state = attributes.get("appStoreState") or attributes.get("appVersionState") or ""
        if state in EDITABLE_STATES:
            return v
    return None


APP_INFO_DOMAIN = Domain(
    name="app_info", group="store", data_file="store/app_info.json",
    resource=APP_INFO_LOCALIZATIONS, title="App name, subtitle, categories",
    pull_fn=_pull_app_info, apply_fn=_apply_app_info,
)

VERSIONS_DOMAIN = Domain(
    name="versions", group="store", data_file="store/versions.json",
    resource=VERSIONS, title="Versions, texts, screenshots, previews, review",
    pull_fn=_pull_versions, apply_fn=_apply_versions,
)

DOMAINS = (APP_INFO_DOMAIN, VERSIONS_DOMAIN)
