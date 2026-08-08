"""Every domain declaration in one place.

The order is a sensible push order: app info before version (a version needs an
editable app info), Game Center before events (events refer to leaderboard
occurrences).
"""
from __future__ import annotations

from typing import Dict, List, Tuple

from . import (accessibility, app_store, events, game_center, iap, pricing,
               privacy, product_pages, subscriptions)
from ..core.registry import Domain

ALL_DOMAINS: Tuple[Domain, ...] = (
    app_store.APP_INFO_DOMAIN,
    app_store.VERSIONS_DOMAIN,
    game_center.ACHIEVEMENTS_DOMAIN,
    game_center.LEADERBOARDS_DOMAIN,
    game_center.LEADERBOARD_SETS_DOMAIN,
    iap.IAP_DOMAIN,
    subscriptions.SUBSCRIPTIONS_DOMAIN,
    events.EVENTS_DOMAIN,
    accessibility.DECLARATIONS_DOMAIN,
    pricing.PRICING_DOMAIN,
    privacy.PRIVACY_DOMAIN,
    product_pages.PAGES_DOMAIN,
)

# CLI groups (--domain store|gamecenter|iap|subscriptions|events|
#             accessibility|pricing|privacy|pages)
GROUPS: Dict[str, List[Domain]] = {}
for _domain in ALL_DOMAINS:
    GROUPS.setdefault(_domain.group or _domain.name, []).append(_domain)


def select(names) -> List[Domain]:
    """Domains for the given --domain arguments; none given means all."""
    if not names:
        return list(ALL_DOMAINS)
    chosen: List[Domain] = []
    for name in names:
        if name in GROUPS:
            chosen.extend(GROUPS[name])
            continue
        match = [d for d in ALL_DOMAINS if d.name == name]
        if not match:
            raise SystemExit(f"Unknown domain '{name}'. Known: "
                             f"{', '.join(sorted(GROUPS))}")
        chosen.extend(match)
    seen = set()
    unique = []
    for domain in chosen:
        if domain.name not in seen:
            seen.add(domain.name)
            unique.append(domain)
    return unique
