"""Accessibility declarations — Apple's accessibility labels on the product page.

One declaration per device family (IPHONE, IPAD, …), each a set of booleans
saying which accessibility features the app supports. Apple shows them on the
product page, so they are content: they belong in the repository next to the
description, not in someone's memory.

The attribute set below was read back from a live declaration, not guessed.
`state` is assigned by ASC (DRAFT until published) and therefore read-only
here.

A note on honesty, since these are claims Apple lets you make about your own
app: every flag you set here is a promise to a person who needs that feature.
`ascsync` cannot check them. Nobody can, apart from you and the people who
rely on them.
"""
from __future__ import annotations

from ..core.registry import Bool, Domain, Enum, Resource

# Apple's device families for this resource. IPHONE covers iPod touch as well.
DEVICE_FAMILIES = ("IPHONE", "IPAD", "APPLE_TV", "APPLE_WATCH", "MAC", "VISION")

# One boolean per supported feature. The names are Apple's.
FEATURES = (
    "supportsAudioDescriptions",
    "supportsCaptions",
    "supportsDarkInterface",
    "supportsDifferentiateWithoutColorAlone",
    "supportsLargerText",
    "supportsReducedMotion",
    "supportsSufficientContrast",
    "supportsVoiceControl",
    "supportsVoiceover",
)

DECLARATIONS = Resource(
    type="accessibilityDeclarations",
    key="deviceFamily",
    root_path="/v1/apps/{app_id}/accessibilityDeclarations",
    parent_rel="app",
    parent_type="apps",
    writable={
        "deviceFamily": Enum(*DEVICE_FAMILIES, immutable=True),
        **{name: Bool() for name in FEATURES},
    },
    # ASC owns the lifecycle: a declaration starts as DRAFT and is published
    # with the next version. Sending it back would be rejected.
    readonly=("state",),
)

DECLARATIONS_DOMAIN = Domain(
    name="accessibility", group="accessibility",
    data_file="accessibility.json", resource=DECLARATIONS,
    title="Accessibility declarations",
    notes="One record per device family. 'state' is assigned by ASC.",
)

DOMAINS = (DECLARATIONS_DOMAIN,)
