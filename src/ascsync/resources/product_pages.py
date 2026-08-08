"""Custom product pages (alternative product pages for campaigns).

Structure in ASC: page -> version -> localization -> its own screenshot sets.
ASC creates the versions itself; here the page and its texts are maintained,
and the screenshots come from assets/ as everywhere else.

Push only with `--allow-pages`, because an active campaign page is publicly
visible and an accidental run changes it immediately.
"""
from __future__ import annotations

from ..core.registry import (AssetSpec, Bool, Domain, ImageRule, Limit,
                             Resource)

PAGE_SCREENSHOTS = AssetSpec(
    name="screenshots",
    api_type="appScreenshots",
    parent_rel="appScreenshotSet",
    parent_type="appCustomProductPageLocalizations",
    relationship="appScreenshots",
    path="pages/{parent_key}/{locale}",
    single=False,
    checksum=True,
    rule=ImageRule(min_width=640, min_height=640, allow_alpha=False),
    set_api_type="appScreenshotSets",
    set_key_attr="screenshotDisplayType",
    set_parent_rel="appCustomProductPageLocalization",
    set_relationship="appScreenshotSets",
)

PAGE_LOCALIZATIONS = Resource(
    type="appCustomProductPageLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    parent_rel="appCustomProductPageVersion",
    parent_type="appCustomProductPageVersions",
    writable={"promotionalText": Limit(170)},
    assets=(PAGE_SCREENSHOTS,),
)

PAGE_VERSIONS = Resource(
    type="appCustomProductPageVersions",
    key="version",
    doc_field="versions",
    list_rel="appCustomProductPageVersions",
    parent_rel="appCustomProductPage",
    parent_type="appCustomProductPages",
    writable={},
    readonly=("state", "deepLink"),
    creatable=False,          # ASC creates the versions
    children=(PAGE_LOCALIZATIONS,),
)

PAGES = Resource(
    type="appCustomProductPages",
    key="name",
    root_path="/v1/apps/{app_id}/appCustomProductPages",
    parent_rel="app",
    parent_type="apps",
    writable={"name": Limit(64), "visible": Bool()},
    children=(PAGE_VERSIONS,),
)

PAGES_DOMAIN = Domain(
    name="pages", group="pages", data_file="store/custom_pages.json",
    resource=PAGES, title="Custom Product Pages",
    push_flag="allow_pages",
    notes="ASC creates the versions; name, visibility, texts and screenshots "
          "are maintained here.",
)

DOMAINS = (PAGES_DOMAIN,)
