"""Prices and availability.

Deliberately cautious: availability (territories) is maintained, prices are
only READ. Price schedules work with territory-specific price point ids
(`appPricePoints`) that differ per currency region, and a wrong push changes
real selling prices. So: pull the current state, click the change in ASC, then
`pull` again.

`push --domain pricing` therefore writes only with `--allow-pricing`, and even
then only the availability.
"""
from __future__ import annotations

from typing import List

from ..core import domains, planner
from ..core.registry import Bool, Domain, Field, Resource

AVAILABILITY = Resource(
    type="appAvailabilityV2",
    key="id",
    singleton=True,
    api_version="v2",
    root_path="/v2/apps/{app_id}/appAvailability",
    writable={"availableInNewTerritories": Bool()},
    readonly=("id",),
    creatable=False,
)


def _pull(engine, ctx: domains.Context, domain: Domain) -> List[dict]:
    """Availability plus the current price schedule (read-only)."""
    items = engine.fetch(AVAILABILITY, app_id=ctx.app_id)
    territories = []
    availability = ctx.client.get_optional(f"/v2/apps/{ctx.app_id}/appAvailability")
    if availability:
        for entry in ctx.client.get_all(
                f"/v2/appAvailabilities/{availability['id']}/territoryAvailabilities"):
            attributes = entry.get("attributes", {}) or {}
            territories.append({
                "territory": ((entry.get("relationships", {}).get("territory", {})
                               .get("data") or {}).get("id")),
                "available": attributes.get("available"),
                "releaseDate": attributes.get("releaseDate"),
            })
    prices = []
    for schedule_kind, path in (
            ("app", f"/v1/apps/{ctx.app_id}/appPriceSchedule"),):
        schedule = ctx.client.get_optional(path)
        if schedule:
            prices.append({"kind": schedule_kind, "id": schedule["id"]})
    if items:
        items[0]["territoryAvailabilities"] = territories
        items[0]["priceSchedules"] = prices
    return items


def _apply(engine, ctx: domains.Context, domain: Domain, plan: planner.Plan) -> None:
    if not ctx.flags.get("allow_pricing"):
        plan.add(planner.BLOCKED, "pricing",
                 "prices and availability are only written with --allow-pricing")
        return
    desired = domains.doc_items(domains.load_doc(domain), domain.resource)
    snapshot_doc = domains.load_doc(domain, snapshot=True)
    snapshot = (domains.doc_items(snapshot_doc, domain.resource)
                if snapshot_doc else None)
    remote = engine.fetch(AVAILABILITY, app_id=ctx.app_id)
    engine.sync(AVAILABILITY, desired, snapshot, remote, plan)
    plan.add(planner.SKIP, "pricing/priceSchedules",
             "price points are deliberately not written — maintain them in ASC, "
             "then run 'ascsync pull --domain pricing'")


PRICING_DOMAIN = Domain(
    name="pricing", group="pricing", data_file="pricing.json",
    resource=AVAILABILITY, title="Availability and prices (prices read-only)",
    push_flag="allow_pricing", pull_fn=_pull, apply_fn=_apply,
)

DOMAINS = (PRICING_DOMAIN,)
