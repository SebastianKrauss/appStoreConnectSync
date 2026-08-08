"""In-app purchases (v2): products, localizations, review screenshot.
Prices live in resources/pricing.py.

productId and inAppPurchaseType are immutable once created — both are only
reported, never patched.
"""
from __future__ import annotations

from ..core import domains
from ..core.registry import (AssetSpec, Bool, Domain, Enum, Field, ImageRule,
                             Limit, RequireTogether, Resource, WarnOnMismatch)

REVIEW_SCREENSHOT = AssetSpec(
    name="reviewScreenshot",
    api_type="inAppPurchaseAppStoreReviewScreenshots",
    parent_rel="inAppPurchaseV2",
    parent_type="inAppPurchases",
    parent_api_version="v2",
    relationship="appStoreReviewScreenshot",
    path="iap/review/{slug}.png",
    checksum=True,
    rule=ImageRule(min_width=640, min_height=920, allow_alpha=False),
)

IAP_LOCALIZATIONS = Resource(
    type="inAppPurchaseLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    parent_rel="inAppPurchaseV2",
    parent_type="inAppPurchases",
    writable={"name": Limit(30), "description": Limit(45)},
    quirks=(RequireTogether(("name",)),),
)

PRODUCTS = Resource(
    type="inAppPurchases",
    key="productId",
    api_version="v2",
    root_path="/v1/apps/{app_id}/inAppPurchasesV2",
    parent_rel="app",
    parent_type="apps",
    writable={
        "productId": Field(immutable=True),
        "inAppPurchaseType": Enum("CONSUMABLE", "NON_CONSUMABLE",
                                  "NON_RENEWING_SUBSCRIPTION", immutable=True),
        "name": Limit(64),                 # interner Referenzname
        "familySharable": Bool(),
        # availableInAllTerritories does NOT belong here: the API answers
        # "'availableInAllTerritories' is not an attribute on the resource
        # 'inAppPurchases'" and discards the entire PATCH — including name and
        # reviewNote in the same call. Availability hangs off its own resource
        # and is maintained together with the prices in ASC.
        "reviewNote": Limit(4000),
    },
    readonly=("state", "contentHosting"),
    children=(IAP_LOCALIZATIONS,),
    assets=(REVIEW_SCREENSHOT,),
    quirks=(WarnOnMismatch("inAppPurchaseType",
                           "the type is immutable once created"),),
)

IAP_DOMAIN = Domain(
    name="iap", group="iap", data_file="iap.json", resource=PRODUCTS,
    title="In-app purchases",
    notes="Prices: see resources/pricing.py. Submitting stays manual.",
)

DOMAINS = (IAP_DOMAIN,)
