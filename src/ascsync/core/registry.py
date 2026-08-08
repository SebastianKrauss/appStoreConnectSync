"""Declarative resource registry.

Every ASC resource is DESCRIBED here rather than programmed; pull, diff,
validation and push fall out of that generically (core/engine.py). A new domain
is a declaration in resources/, not a new script.

Vocabulary:
  Field      a writable attribute, with its limit, type and mutability
  Resource   an ASC resource, with children, assets and state gates
  AssetSpec  a file relationship (image or video) on a resource
  Quirk      a named special case (e.g. whatsNew on a first version)
"""
from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Any, Callable, Dict, Optional, Tuple

# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------
TEXT, BOOL, INT, URL, ENUM, DATE, LIST = "text", "bool", "int", "url", "enum", "date", "list"


@dataclass(frozen=True)
class Field:
    limit: Optional[int] = None
    kind: str = TEXT
    choices: Tuple[str, ...] = ()
    required: bool = False
    immutable: bool = False   # settable only on create (e.g. productId)
    skip_if_empty: bool = True  # empty value = leave the ASC state alone
    # Optional for the API, required for SUBMISSION (support URL, privacy URL,
    # review contact, …). Checked by `ascsync validate --readiness`.
    submission: bool = False


def Limit(n: int, submission: bool = False) -> Field:
    return Field(limit=n, submission=submission)


def Url(submission: bool = False) -> Field:
    return Field(kind=URL, submission=submission)


def Bool(required: bool = False) -> Field:
    return Field(kind=BOOL, required=required, skip_if_empty=False)


def Int(required: bool = False) -> Field:
    return Field(kind=INT, required=required, skip_if_empty=False)


def Enum(*choices: str, **kw) -> Field:
    return Field(kind=ENUM, choices=tuple(choices), **kw)


def Date() -> Field:
    return Field(kind=DATE)


# ---------------------------------------------------------------------------
# Assets
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ImageRule:
    """Requirements on an image or video file that can be checked offline."""
    min_width: int = 0
    min_height: int = 0
    max_width: int = 0
    max_height: int = 0
    exact: Tuple[Tuple[int, int], ...] = ()      # allowed sizes (empty = any)
    aspect: Optional[float] = None               # width/height
    aspect_tolerance: float = 0.01
    allow_alpha: bool = False
    formats: Tuple[str, ...] = (".png", ".jpg", ".jpeg")


@dataclass(frozen=True)
class AssetSpec:
    """A file relationship on a resource.

    single=True  → exactly one asset (achievement or leaderboard icon, event card)
    single=False → an ordered set (screenshots, previews) with its own set resource
    """
    name: str                    # label in the report, e.g. "image", "card"
    api_type: str                # e.g. "gameCenterAchievementImages"
    parent_rel: str              # relationship name in the POST body
    parent_type: str             # JSON:API type of the parent
    relationship: str            # path segment on the parent, for reading
    path: str                    # template relative to assets/, e.g. "gamecenter/achievements/{slug}.png"
    single: bool = True
    checksum: bool = False       # sourceFileChecksum is supported
    api_version: str = "v1"      # API version of the asset resource itself
    parent_api_version: str = "v1"   # API version of the parent path (IAP: v2)
    rule: Optional[ImageRule] = None
    # single=False only:
    set_api_type: str = ""       # e.g. "appScreenshotSets"
    set_key_attr: str = ""       # e.g. "screenshotDisplayType"
    set_parent_rel: str = ""     # e.g. "appStoreVersionLocalization"
    set_relationship: str = ""   # e.g. "appScreenshotSets"
    fallbacks: Tuple[str, ...] = ()   # further path templates; first hit wins
    # Several assets of the same kind on ONE resource, told apart by an
    # attribute (in-app events: card and detail page both hang off the
    # localization):
    type_attr: str = ""          # e.g. "appEventAssetType"
    type_value: str = ""         # e.g. "EVENT_CARD"
    video_api_type: str = ""     # if the file is a video, e.g. "appEventVideoClips"


# ---------------------------------------------------------------------------
# Quirks — named special cases instead of if-branches scattered around
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class SkipFieldOnError:
    """Drop the field on error and retry (e.g. whatsNew on a very first app
    version — Apple rejects it there)."""
    field: str
    reason: str


@dataclass(frozen=True)
class RequireTogether:
    """Write these fields only when ALL of them are filled: skip an incomplete
    localization rather than writing half of it."""
    fields: Tuple[str, ...]


@dataclass(frozen=True)
class WarnOnMismatch:
    """Immutable field: report a mismatch, never patch it."""
    field: str
    reason: str


# ---------------------------------------------------------------------------
# Ressourcen
# ---------------------------------------------------------------------------
@dataclass
class Resource:
    type: str                                  # JSON:API type = URL segment
    key: str                                   # natural key (an attribute)
    writable: Dict[str, Field] = dc_field(default_factory=dict)
    readonly: Tuple[str, ...] = ()
    children: Tuple["Resource", ...] = ()
    assets: Tuple[AssetSpec, ...] = ()
    quirks: Tuple[Any, ...] = ()
    api_version: str = "v1"
    root_path: str = ""            # Wurzel: Listenpfad, z. B. "/v1/apps/{app_id}/appStoreVersions"
    list_rel: str = ""             # Kind: Pfadsegment am Parent (Default: type)
    parent_rel: str = ""           # Beziehungsname im POST-Body
    parent_type: str = ""          # JSON:API-Typ des Parents
    doc_field: str = ""            # Feldname im data/*.json (Default: type)
    keyed: bool = False            # child collection as a dict (locale → {...}), not a list
    creatable: bool = True
    deletable: bool = False        # delete overhangs in ASC? (ordered sets only)
    editable_states: Optional[frozenset] = None   # state gate on the resource's own state
    singleton: bool = False        # 1:1 relationship with no key of its own

    # -- paths -------------------------------------------------------------
    def collection_path(self) -> str:
        return f"/{self.api_version}/{self.type}"

    def item_path(self, item_id: str) -> str:
        return f"/{self.api_version}/{self.type}/{item_id}"

    def child_list_path(self, parent_type: str, parent_id: str,
                        parent_version: str = "v1") -> str:
        return f"/{parent_version}/{parent_type}/{parent_id}/{self.list_rel or self.type}"

    def doc_key(self) -> str:
        return self.doc_field or self.type

    def quirk(self, cls) -> list:
        return [q for q in self.quirks if isinstance(q, cls)]


@dataclass
class Domain:
    """One file under data/ plus the root resource inside it.

    `pull_fn` and `apply_fn` are optional: domains whose parent is simply the
    app get by with the generic drivers (core/domains.py). Store and Game
    Center bring their own, because their parent has to be resolved first.
    """
    name: str                      # "store", "gamecenter", "iap", "events", ...
    data_file: str                 # relativ zu data/, z. B. "gamecenter/achievements.json"
    resource: Resource
    title: str = ""
    push_flag: str = ""            # needs an extra CLI flag (e.g. prices)
    notes: str = ""
    group: str = ""                # CLI domain name (several files per group)
    pull_fn: Optional[Callable] = None
    apply_fn: Optional[Callable] = None


def walk(resource: Resource):
    """A resource and all of its children (depth first)."""
    yield resource
    for child in resource.children:
        for r in walk(child):
            yield r
