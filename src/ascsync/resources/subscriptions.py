"""Auto-renewable subscriptions: groups, subscriptions, localizations.

The structure ASC uses:

    subscriptionGroup            (referenceName)
      ├── localizations          (locale: name, customAppName)
      └── subscriptions          (productId)
            ├── localizations    (locale: name, description)
            ├── review screenshot
            └── prices           ← read-only here, like every other price

A group holds the subscriptions a customer can switch between; `groupLevel`
decides what counts as an upgrade and what as a downgrade. Every subscription
needs a group, even if it is alone in it.

WHAT IS NOT HERE, AND WHY

Introductory offers, promotional offers, offer codes and price schedules are
deliberately absent. They are priced per territory through price-point ids that
differ per currency region, and a wrong write changes what real customers pay.
The same reasoning as resources/pricing.py: read them, click the change in ASC,
pull afterwards.

A CAVEAT WORTH READING

Unlike every other declaration in this directory, this one was written against
Apple's documented model rather than against a live subscription — the project
this tool grew out of has none. The paths are verified (they answer 200), the
field names are not. If a push reports "is not an attribute", the fault is
here, and `.snapshot/` after a pull tells you the truth about the fields ASC
actually knows.
"""
from __future__ import annotations

from ..core.registry import (AssetSpec, Bool, Domain, Enum, Field, ImageRule,
                             Int, Limit, RequireTogether, Resource,
                             WarnOnMismatch)

# Apple's renewal periods.
PERIODS = ("ONE_WEEK", "ONE_MONTH", "TWO_MONTHS", "THREE_MONTHS",
           "SIX_MONTHS", "ONE_YEAR")

REVIEW_SCREENSHOT = AssetSpec(
    name="reviewScreenshot",
    api_type="subscriptionAppStoreReviewScreenshots",
    parent_rel="subscription",
    parent_type="subscriptions",
    relationship="appStoreReviewScreenshot",
    path="subscriptions/review/{slug}.png",
    checksum=True,
    rule=ImageRule(min_width=640, min_height=640, allow_alpha=False),
)

SUBSCRIPTION_LOCALIZATIONS = Resource(
    type="subscriptionLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    list_rel="subscriptionLocalizations",
    parent_rel="subscription",
    parent_type="subscriptions",
    writable={
        "name": Limit(30, submission=True),
        "description": Limit(45),
    },
    # An incomplete localization is worse than none: the push would write half
    # a record and ASC would show a subscription with a name and no text.
    quirks=(RequireTogether(("name", "description")),),
)

SUBSCRIPTIONS = Resource(
    type="subscriptions",
    key="productId",
    doc_field="subscriptions",
    list_rel="subscriptions",
    parent_rel="group",
    parent_type="subscriptionGroups",
    writable={
        "productId": Field(immutable=True),
        "name": Limit(64),                    # internal reference name
        "subscriptionPeriod": Enum(*PERIODS),
        "familySharable": Bool(),
        "groupLevel": Int(),
        "reviewNote": Limit(4000),
    },
    readonly=("state",),
    children=(SUBSCRIPTION_LOCALIZATIONS,),
    assets=(REVIEW_SCREENSHOT,),
    quirks=(WarnOnMismatch("productId",
                           "the product id is immutable once created"),),
)

GROUP_LOCALIZATIONS = Resource(
    type="subscriptionGroupLocalizations",
    key="locale",
    doc_field="localizations",
    keyed=True,
    list_rel="subscriptionGroupLocalizations",
    parent_rel="subscriptionGroup",
    parent_type="subscriptionGroups",
    writable={
        # What the customer sees above the choice of subscriptions.
        "name": Limit(30, submission=True),
        "customAppName": Limit(30),
    },
)

GROUPS = Resource(
    type="subscriptionGroups",
    key="referenceName",
    root_path="/v1/apps/{app_id}/subscriptionGroups",
    parent_rel="app",
    parent_type="apps",
    writable={
        "referenceName": Limit(64, submission=True),
    },
    children=(GROUP_LOCALIZATIONS, SUBSCRIPTIONS),
)

SUBSCRIPTIONS_DOMAIN = Domain(
    name="subscriptions", group="subscriptions",
    data_file="subscriptions.json", resource=GROUPS,
    title="Subscriptions",
    notes="Prices and offers stay in ASC; see the module docstring.",
)

DOMAINS = (SUBSCRIPTIONS_DOMAIN,)
