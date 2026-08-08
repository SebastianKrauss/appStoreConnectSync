"""App privacy (the data disclosures on the product page).

Two peculiarities that have to stay visible in the data model:
  1. `appDataUsages` are pure relationship triples (category x purpose x
     protection), not attributes — hence the flat representation in
     data/privacy.json.
  2. Publishing is a SEPARATE step (`appDataUsagesPublishState`), which is why
     it has its own command: `ascsync privacy publish`.

Note: this relationship is not exposed for every app. If a pull answers 404
("the relationship 'dataUsages' does not exist"), app privacy has to be
maintained in the web interface and this file only documents it.
"""
from __future__ import annotations

from typing import List

from ..core import domains, planner
from ..core.registry import Domain, Resource

USAGES = Resource(
    type="appDataUsages",
    key="key",
    root_path="/v1/apps/{app_id}/dataUsages",
    parent_rel="app",
    parent_type="apps",
    writable={},
    creatable=False,
)


def _triple(entry: dict) -> str:
    rel = entry.get("relationships", {}) or {}

    def rid(name):
        return ((rel.get(name) or {}).get("data") or {}).get("id") or ""
    return "|".join((rid("category"), rid("purpose"), rid("dataProtection")))


def _pull(engine, ctx: domains.Context, domain: Domain) -> List[dict]:
    items = []
    for entry in ctx.client.get_all(f"/v1/apps/{ctx.app_id}/dataUsages"):
        items.append({"key": _triple(entry), "readonly": {"id": entry["id"]}})
    state = ctx.client.get_optional(f"/v1/apps/{ctx.app_id}/appDataUsagesPublishState")
    if state:
        items.append({"key": "publishState",
                      "readonly": {"id": state["id"],
                                   "published": (state.get("attributes", {}) or {})
                                   .get("published")}})
    return items


def _apply(engine, ctx: domains.Context, domain: Domain, plan: planner.Plan) -> None:
    desired = {str(i.get("key")) for i in
               domains.doc_items(domains.load_doc(domain), domain.resource)}
    remote_items = _pull(engine, ctx, domain)
    remote = {str(i.get("key")) for i in remote_items}
    for key in sorted(desired - remote - {"publishState"}):
        plan.add(planner.CREATE, f"dataUsages/{key}", "Kategorie x Zweck x Schutz",
                 executed=not ctx.client.dry_run)
        category, purpose, protection = (key.split("|") + ["", "", ""])[:3]
        relationships = {"app": {"data": {"type": "apps", "id": ctx.app_id}}}
        if category:
            relationships["category"] = {"data": {"type": "appDataUsageCategories",
                                                  "id": category}}
        if purpose:
            relationships["purpose"] = {"data": {"type": "appDataUsagePurposes",
                                                 "id": purpose}}
        if protection:
            relationships["dataProtection"] = {"data": {"type": "appDataUsageDataProtections",
                                                        "id": protection}}
        ctx.client.post("/v1/appDataUsages",
                        {"data": {"type": "appDataUsages", "relationships": relationships}})
    for key in sorted(remote - desired - {"publishState"}):
        plan.add(planner.OVERHANG, f"dataUsages/{key}",
                 "in ASC, not in data/privacy.json — will not be deleted")
    plan.add(planner.SKIP, "privacy/publish",
             "publishing is its own step: 'ascsync privacy publish'")


def publish(ctx: domains.Context, plan: planner.Plan) -> None:
    state = ctx.client.get_optional(f"/v1/apps/{ctx.app_id}/appDataUsagesPublishState")
    if not state:
        plan.add(planner.SKIP, "privacy/publish", "no publish object in ASC")
        return
    if (state.get("attributes", {}) or {}).get("published"):
        plan.add(planner.NOOP, "privacy/publish", "already published")
        return
    plan.add(planner.UPDATE, "privacy/publish", fields={"published": True},
             executed=not ctx.client.dry_run)
    ctx.client.patch(f"/v1/appDataUsagesPublishState/{state['id']}", {
        "data": {"type": "appDataUsagesPublishState", "id": state["id"],
                 "attributes": {"published": True}}
    })


PRIVACY_DOMAIN = Domain(
    name="privacy", group="privacy", data_file="privacy.json",
    resource=USAGES, title="App privacy (data usage)",
    pull_fn=_pull, apply_fn=_apply,
)

DOMAINS = (PRIVACY_DOMAIN,)
