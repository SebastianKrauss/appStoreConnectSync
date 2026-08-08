"""The link between registry and files: context, generic pull, generic
plan/push, document format.

Document format (data/*.json and .snapshot/*.json have the same shape):

    {
      "resource": "gameCenterAchievements",
      "key": "vendorIdentifier",
      "items": [ { …attributes…, "localizations": { "en-US": {…} } } ]
    }

`items` is a list — or a dict when the natural key is a language (`keyed`).
ASC ids live only in the snapshot: writing to data/ strips `readonly.id`.
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

from . import differ, paths, planner
from .registry import Domain, Resource


@dataclass
class Context:
    """Everything a domain driver needs to know about the app."""
    client: object
    app_id: str
    app: dict
    locales: List[str]
    version: Optional[str] = None        # gewuenschter versionString (--version)
    flags: Dict[str, bool] = field(default_factory=dict)
    cache: Dict[str, object] = field(default_factory=dict)

    @property
    def bundle_id(self) -> str:
        return self.app["bundleId"]


# ---------------------------------------------------------------------------
# Dokumente lesen/schreiben
# ---------------------------------------------------------------------------
def empty_doc(resource: Resource) -> dict:
    return {"resource": resource.type, "key": resource.key,
            "items": {} if resource.keyed else []}


def doc_items(doc: Optional[dict], resource: Resource) -> List[dict]:
    if not doc:
        return []
    raw = doc.get("items")
    if raw is None:
        return []
    if resource.keyed and isinstance(raw, dict):
        return differ.keyed_to_items(raw, resource.key)
    if resource.singleton and isinstance(raw, dict):
        return [raw]
    return list(raw)


def pack_doc(resource: Resource, items: List[dict], strip_ids: bool) -> dict:
    items = [_strip(copy.deepcopy(i), strip_ids) for i in items]
    if resource.keyed:
        return {"resource": resource.type, "key": resource.key,
                "items": differ.items_to_keyed(items, resource.key)}
    if resource.singleton:
        return {"resource": resource.type, "key": resource.key,
                "items": items[0] if items else {}}
    return {"resource": resource.type, "key": resource.key, "items": items}


def _strip(item: dict, strip_ids: bool) -> dict:
    if strip_ids and isinstance(item.get("readonly"), dict):
        item["readonly"] = {k: v for k, v in item["readonly"].items() if k != "id"}
        if not item["readonly"]:
            del item["readonly"]
    for value in list(item.values()):
        if isinstance(value, list):
            for entry in value:
                if isinstance(entry, dict):
                    _strip(entry, strip_ids)
        elif isinstance(value, dict):
            for entry in value.values():
                if isinstance(entry, dict):
                    _strip(entry, strip_ids)
    return item


def load_doc(domain: Domain, snapshot: bool = False) -> Optional[dict]:
    path = (paths.snapshot_path(domain.data_file) if snapshot
            else paths.data_path(domain.data_file))
    if not os.path.exists(path):
        return None
    return paths.read_json(path)


def save_doc(domain: Domain, doc: dict, snapshot: bool = False) -> str:
    path = (paths.snapshot_path(domain.data_file) if snapshot
            else paths.data_path(domain.data_file))
    paths.write_json(path, doc)
    return path


def merge_into_data(domain: Domain, remote_items: List[dict]) -> dict:
    """Pull into data/: take the ASC state, keep extra local fields.

    Deliberately conservative — fields ASC does not know (generator metadata
    such as assetVariant, for instance) survive.
    """
    resource = domain.resource
    existing = {str(i.get(resource.key)): i
                for i in doc_items(load_doc(domain), resource)}
    merged: List[dict] = []
    for item in remote_items:
        key = str(item.get(resource.key))
        base = copy.deepcopy(existing.get(key, {}))
        base.update(copy.deepcopy(item))
        _merge_children(base, existing.get(key, {}), item, resource)
        merged.append(base)
    for key, item in existing.items():
        if key not in {str(i.get(resource.key)) for i in remote_items}:
            item = copy.deepcopy(item)
            item.setdefault("readonly", {})["onlyLocal"] = True
            merged.append(item)
    return pack_doc(resource, merged, strip_ids=True)


def _merge_children(target: dict, old: dict, new: dict, resource: Resource) -> None:
    for child in resource.children:
        field_name = child.doc_key()
        old_child = (old or {}).get(field_name)
        new_child = new.get(field_name)
        if isinstance(old_child, dict) and isinstance(new_child, dict):
            merged = copy.deepcopy(old_child)
            for key, value in new_child.items():
                entry = copy.deepcopy(merged.get(key, {}))
                entry.update(copy.deepcopy(value))
                merged[key] = entry
            target[field_name] = merged


# ---------------------------------------------------------------------------
# Generische Treiber
# ---------------------------------------------------------------------------
PullFn = Callable[..., List[dict]]


def generic_pull(engine, ctx: Context, domain: Domain) -> List[dict]:
    return engine.fetch(domain.resource, app_id=ctx.app_id)


def generic_apply(engine, ctx: Context, domain: Domain, plan: planner.Plan) -> None:
    desired = doc_items(load_doc(domain), domain.resource)
    snapshot_doc = load_doc(domain, snapshot=True)
    snapshot = doc_items(snapshot_doc, domain.resource) if snapshot_doc else None
    if snapshot is None:
        plan.add(planner.SKIP, domain.name,
                 "no snapshot — run 'ascsync pull' before the first push")
    remote = engine.fetch(domain.resource, app_id=ctx.app_id)
    # On create the API demands the parent relationship ("You must provide a
    # value for the relationship 'app'"). Reading works without it via
    # root_path, so this only surfaces on the first genuine CREATE.
    parent_id = ctx.app_id if (domain.resource.parent_type or "") == "apps" else ""
    engine.sync(domain.resource, desired, snapshot, remote, plan, "",
                parent_id, "apps" if parent_id else "")
