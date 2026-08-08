"""The generic walker: read ASC, compare against data/ and .snapshot/, and
write — or only plan.

`plan` and `push` run the same code; the only difference is `client.dry_run`,
so a dry run cannot diverge from the real thing. The dry run also walks NEW
objects all the way through: children and assets are planned against a
placeholder id instead of disappearing from the output.
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import assets as assetlib
from . import differ, paths, planner
from .client import ApiError, state_of
from .registry import (AssetSpec, RequireTogether, Resource, SkipFieldOnError,
                       WarnOnMismatch)

NEW = "<new>"          # placeholder id for objects created during a dry run


class Engine:
    def __init__(self, client, report, lock: Optional[assetlib.AssetLock] = None,
                 skip_assets: bool = False, only_keys=(), only_locales=()):
        self.client = client
        self.report = report
        self.lock = lock or assetlib.AssetLock()
        self.skip_assets = skip_assets
        self.only_keys = set(only_keys or ())
        self.only_locales = set(only_locales or ())

    # =======================================================================
    # Lesen (pull)
    # =======================================================================
    def fetch(self, resource: Resource, parent_type: str = "", parent_id: str = "",
              parent_version: str = "v1", app_id: str = "") -> List[dict]:
        """Read a resource from ASC, with its children and asset state."""
        if resource.root_path:
            path = resource.root_path.format(app_id=app_id)
        else:
            path = resource.child_list_path(parent_type, parent_id, parent_version)
        try:
            if resource.singleton:
                data = self.client.get_optional(path)
                raw = [data] if data else []
            else:
                raw = self.client.get_all(path)
        except ApiError as e:
            if e.status in (404, 403):
                return []
            raise
        items = []
        for entry in raw:
            items.append(self._flatten(resource, entry))
        return items

    def _flatten(self, resource: Resource, entry: dict) -> dict:
        attributes = entry.get("attributes", {}) or {}
        item: Dict[str, Any] = {}
        if not resource.singleton:
            item[resource.key] = attributes.get(resource.key)
        for name in resource.writable:
            if name in attributes:
                item[name] = attributes[name]
        readonly = {"id": entry["id"]}
        for name in resource.readonly:
            if name in attributes:
                readonly[name] = attributes[name]
        state = state_of(entry)
        if state:
            readonly["state"] = state
        item["readonly"] = readonly
        for child in resource.children:
            child_items = self.fetch(child, resource.type, entry["id"], resource.api_version)
            item[child.doc_key()] = (differ.items_to_keyed(child_items, child.key)
                                     if child.keyed else child_items)
        return item

    # =======================================================================
    # Compare and write (plan / push)
    # =======================================================================
    def sync(self, resource: Resource, desired_items: List[dict],
             snapshot_items: Optional[List[dict]], remote_items: List[dict],
             plan: planner.Plan, path_prefix: str = "", parent_id: str = "",
             parent_type: str = "", fmt: Optional[dict] = None,
             blocked: str = "") -> None:
        fmt = dict(fmt or {})
        remote_by_key = differ.index_by_key(remote_items, resource.key)
        snapshot_by_key = (differ.index_by_key(snapshot_items, resource.key)
                           if snapshot_items is not None else None)

        for desired in desired_items:
            key = str(desired.get(resource.key, ""))
            if resource.singleton:
                key = resource.type
            if self.only_keys and not path_prefix and key not in self.only_keys:
                continue
            if self.only_locales and resource.key == "locale" and key not in self.only_locales:
                continue
            item_path = f"{path_prefix}{key}" if path_prefix else key
            remote = remote_by_key.get(key) if not resource.singleton else (
                remote_items[0] if remote_items else None)
            snapshot = None
            if snapshot_by_key is not None:
                snapshot = (snapshot_by_key.get(key) if not resource.singleton
                            else (snapshot_items[0] if snapshot_items else None))

            item_fmt = dict(fmt)
            item_fmt.update({k: v for k, v in desired.items() if isinstance(v, str)})
            item_fmt["key"] = key
            item_fmt["slug"] = slugify(key)

            remote_id = self._apply_item(resource, desired, snapshot, remote, plan,
                                         item_path, parent_id, parent_type, blocked)
            if remote_id is None:
                continue

            child_blocked = blocked or self._state_block(resource, remote)
            child_fmt = dict(item_fmt)
            child_fmt["parent_key"] = key
            child_fmt["parent_slug"] = item_fmt["slug"]
            for child in resource.children:
                desired_children = collection_items(desired, child)
                snapshot_children = (collection_items(snapshot, child)
                                     if snapshot is not None else None)
                remote_children = (collection_items(remote, child) if remote
                                   else ([] if remote_id != NEW else []))
                self.sync(child, desired_children, snapshot_children, remote_children,
                          plan, f"{item_path}/{child.doc_key()}/", remote_id,
                          resource.type, child_fmt, child_blocked)

            if not self.skip_assets:
                for spec in resource.assets:
                    self._sync_asset(spec, remote, remote_id, plan, item_path,
                                     item_fmt, child_blocked)

        # Overhangs: present in ASC, absent locally
        desired_keys = {str(d.get(resource.key, "")) for d in desired_items}
        if not resource.singleton:
            for key, remote in remote_by_key.items():
                if key in desired_keys:
                    continue
                if self.only_keys and not path_prefix and key not in self.only_keys:
                    continue
                item_path = f"{path_prefix}{key}" if path_prefix else key
                if resource.deletable:
                    plan.add(planner.DELETE, item_path, "no longer present locally")
                    if not self.client.dry_run:
                        self.client.delete(resource.item_path(remote["readonly"]["id"]))
                        plan.actions[-1].executed = True
                else:
                    plan.add(planner.OVERHANG, item_path,
                             "in ASC, not in data/ — will not be deleted")

    # -- a single record ---------------------------------------------------
    def _apply_item(self, resource: Resource, desired: dict, snapshot: Optional[dict],
                    remote: Optional[dict], plan: planner.Plan, item_path: str,
                    parent_id: str, parent_type: str, blocked: str) -> Optional[str]:
        """Creates or patches; returns the remote id (NEW on a dry run)."""
        for quirk in resource.quirk(RequireTogether):
            if any(not str(desired.get(f) or "").strip() for f in quirk.fields):
                plan.add(planner.SKIP, item_path,
                         f"incomplete ({', '.join(quirk.fields)}) — not written")
                return remote["readonly"]["id"] if remote else None

        attributes = {name: desired[name] for name in resource.writable
                      if name in desired}

        if remote is None:
            if not resource.creatable:
                plan.add(planner.SKIP, item_path, "has to be created in ASC")
                return None
            if blocked:
                plan.add(planner.BLOCKED, item_path, blocked)
                return None
            body_attrs = {k: v for k, v in attributes.items()
                          if v is not None and v != ""}
            if not resource.singleton:
                body_attrs[resource.key] = desired.get(resource.key)
            plan.add(planner.CREATE, item_path, fields=body_attrs,
                     executed=not self.client.dry_run)
            body = {"data": {"type": resource.type, "attributes": body_attrs}}
            if parent_id and resource.parent_rel:
                body["data"]["relationships"] = {
                    resource.parent_rel: {"data": {"type": resource.parent_type or parent_type,
                                                   "id": parent_id}}}
            created = self._post_with_quirks(resource, body, plan, item_path)
            if created is None:
                return NEW if self.client.dry_run else None
            return created["data"]["id"]

        remote_id = remote["readonly"]["id"]
        # Immutable fields: report only
        for quirk in resource.quirk(WarnOnMismatch):
            name = quirk.field
            if name in desired and desired[name] != remote.get(name):
                plan.add(planner.SKIP, f"{item_path}/{name}",
                         f"{quirk.reason} (ASC: {remote.get(name)!r}, "
                         f"lokal: {desired[name]!r})")
        writable = {n: f for n, f in resource.writable.items() if not f.immutable}
        to_write, drift, conflict = differ.diff_attributes(
            desired, snapshot, remote, writable)
        for name, (remote_value, snapshot_value) in drift.items():
            plan.add(planner.DRIFT, f"{item_path}/{name}",
                     f"changed in ASC ({snapshot_value!r} -> {remote_value!r}) — "
                     f"'ascsync pull' will take it")
        for name, (desired_value, remote_value) in conflict.items():
            plan.add(planner.CONFLICT, f"{item_path}/{name}",
                     f"local {desired_value!r} vs. ASC {remote_value!r} — not written")
        if not to_write:
            if not drift and not conflict:
                plan.add(planner.NOOP, item_path)
            return remote_id
        if blocked:
            plan.add(planner.BLOCKED, item_path, blocked, fields=to_write)
            return remote_id
        plan.add(planner.UPDATE, item_path, fields=to_write,
                 executed=not self.client.dry_run)
        body = {"data": {"type": resource.type, "id": remote_id, "attributes": to_write}}
        self._patch_with_quirks(resource, resource.item_path(remote_id), body,
                                plan, item_path)
        return remote_id

    def _post_with_quirks(self, resource: Resource, body: dict,
                          plan: planner.Plan, item_path: str):
        try:
            return self.client.post(resource.collection_path(), body)
        except ApiError as e:
            retry = self._without_quirky_field(resource, body["data"]["attributes"], e,
                                               plan, item_path)
            if retry is None:
                plan.add(planner.ERROR, item_path, e.summary())
                return None
            body["data"]["attributes"] = retry
            try:
                return self.client.post(resource.collection_path(), body)
            except ApiError as e2:
                plan.add(planner.ERROR, item_path, e2.summary())
                return None

    def _patch_with_quirks(self, resource: Resource, path: str, body: dict,
                           plan: planner.Plan, item_path: str) -> None:
        try:
            self.client.patch(path, body)
        except ApiError as e:
            retry = self._without_quirky_field(resource, body["data"]["attributes"], e,
                                               plan, item_path)
            if retry is None:
                plan.add(planner.ERROR, item_path, e.summary())
                return
            body["data"]["attributes"] = retry
            try:
                self.client.patch(path, body)
            except ApiError as e2:
                plan.add(planner.ERROR, item_path, e2.summary())

    def _without_quirky_field(self, resource: Resource, attributes: dict,
                              error: ApiError, plan: planner.Plan,
                              item_path: str) -> Optional[dict]:
        """SkipFieldOnError: drop the known problem field and say so."""
        for quirk in resource.quirk(SkipFieldOnError):
            if quirk.field in attributes and (error.mentions(quirk.field)
                                              or len(attributes) > 1):
                plan.add(planner.SKIP, f"{item_path}/{quirk.field}", quirk.reason)
                remaining = {k: v for k, v in attributes.items() if k != quirk.field}
                return remaining or None
        return None

    def _state_block(self, resource: Resource, remote: Optional[dict]) -> str:
        """State gate: returns a reason when writing here is not allowed."""
        if not resource.editable_states or not remote:
            return ""
        state = (remote.get("readonly") or {}).get("state", "")
        if state and state not in resource.editable_states:
            return f"state {state} does not allow changes"
        return ""

    # =======================================================================
    # Assets
    # =======================================================================
    def _sync_asset(self, spec: AssetSpec, remote: Optional[dict], parent_id: str,
                    plan: planner.Plan, item_path: str, fmt: dict, blocked: str) -> None:
        if spec.single:
            self._sync_single_asset(spec, parent_id, plan, item_path, fmt, blocked)
        else:
            self._sync_asset_set(spec, parent_id, plan, item_path, fmt, blocked)

    def _sync_single_asset(self, spec: AssetSpec, parent_id: str, plan: planner.Plan,
                           item_path: str, fmt: dict, blocked: str) -> None:
        local = assetlib.resolve_asset(spec.path, spec.fallbacks, **fmt)
        asset_path = f"{item_path}/{spec.name}"
        if not local:
            try:
                expected = spec.path.format(**fmt)
            except KeyError:
                expected = spec.path
            plan.add(planner.SKIP, asset_path, f"no file at assets/{expected}")
            return
        checksum = assetlib.md5_of(local)
        lock_key = paths.rel_to_asc(local)
        # A video instead of an image: different resource, same mechanics.
        api_type = (spec.video_api_type if assetlib.is_video(local) and spec.video_api_type
                    else spec.api_type)
        relationship = (spec.video_api_type if assetlib.is_video(local) and spec.video_api_type
                        else spec.relationship)
        extra = {spec.type_attr: spec.type_value} if spec.type_attr else None

        remote_asset = None
        if parent_id and parent_id != NEW:
            remote_asset = self._remote_asset(spec, relationship, parent_id)
        if remote_asset and assetlib.is_complete(remote_asset):
            attributes = remote_asset.get("attributes", {}) or {}
            same = (attributes.get("sourceFileChecksum") == checksum if spec.checksum
                    else self.lock.matches(lock_key, checksum, remote_asset["id"]))
            if same:
                plan.add(planner.NOOP, asset_path, "unchanged")
                return
            plan.add(planner.DELETE, asset_path, "changed — will be replaced",
                     executed=not self.client.dry_run)
            if not self.client.dry_run:
                self.client.delete(f"/{spec.api_version}/{api_type}/{remote_asset['id']}")
        elif remote_asset:
            plan.add(planner.DELETE, asset_path, "incomplete — will be replaced",
                     executed=not self.client.dry_run)
            if not self.client.dry_run:
                self.client.delete(f"/{spec.api_version}/{api_type}/{remote_asset['id']}")

        if blocked:
            plan.add(planner.BLOCKED, asset_path, blocked)
            return
        plan.add(planner.UPLOAD, asset_path, os.path.basename(local),
                 executed=not self.client.dry_run)
        if self.client.dry_run or parent_id == NEW:
            return
        asset_id = assetlib.upload_asset(
            self.client, api_type, spec.api_version, spec.parent_rel,
            spec.parent_type, parent_id, local, checksum if spec.checksum else None,
            extra_attributes=extra)
        if asset_id and not spec.checksum:
            self.lock.remember(lock_key, checksum, asset_id)

    def _remote_asset(self, spec: AssetSpec, relationship: str,
                      parent_id: str) -> Optional[dict]:
        """The existing asset — for typed sets (event card vs. detail page)
        the one whose type attribute matches."""
        path = f"/{spec.parent_api_version}/{spec.parent_type}/{parent_id}/{relationship}"
        if not spec.type_attr:
            return self.client.get_optional(path)
        try:
            for entry in self.client.get_all(path):
                if (entry.get("attributes", {}) or {}).get(spec.type_attr) == spec.type_value:
                    return entry
        except ApiError as e:
            if e.status in (404, 403):
                return None
            raise
        return None

    def _sync_asset_set(self, spec: AssetSpec, parent_id: str, plan: planner.Plan,
                        item_path: str, fmt: dict, blocked: str) -> None:
        """An ordered set (screenshots, previews): one subdirectory per display type."""
        try:
            base = paths.asset_path(spec.path.format(**fmt))
        except KeyError:
            return
        if not os.path.isdir(base):
            plan.add(planner.SKIP, f"{item_path}/{spec.name}",
                     f"no directory assets/{spec.path.format(**fmt)}")
            return
        display_types = sorted(d for d in os.listdir(base)
                               if os.path.isdir(os.path.join(base, d)) and not d.startswith("."))
        remote_sets = {}
        if parent_id and parent_id != NEW:
            for entry in self.client.get_all(
                    f"/{spec.parent_api_version}/{spec.parent_type}/{parent_id}/{spec.set_relationship}"):
                remote_sets[(entry.get("attributes", {}) or {}).get(spec.set_key_attr)] = entry

        for display_type in display_types:
            set_path = f"{item_path}/{spec.name}/{display_type}"
            files = assetlib.ordered_files(os.path.join(base, display_type),
                                           (spec.rule.formats if spec.rule else (".png", ".jpg"))
                                           + assetlib.VIDEO_EXTENSIONS)
            if not files:
                continue
            if blocked:
                plan.add(planner.BLOCKED, set_path, blocked)
                continue
            entry = remote_sets.pop(display_type, None)
            if entry is None:
                plan.add(planner.CREATE, set_path, f"{spec.set_api_type} anlegen",
                         executed=not self.client.dry_run)
                created = self.client.post(f"/{spec.api_version}/{spec.set_api_type}", {
                    "data": {
                        "type": spec.set_api_type,
                        "attributes": {spec.set_key_attr: display_type},
                        "relationships": {spec.set_parent_rel: {
                            "data": {"type": spec.parent_type, "id": parent_id}}},
                    }
                }) if parent_id != NEW else None
                set_id = created["data"]["id"] if created else NEW
            else:
                set_id = entry["id"]

            remote_assets = {}
            if set_id != NEW:
                for asset in self.client.get_all(
                        f"/{spec.api_version}/{spec.set_api_type}/{set_id}/{spec.relationship}"):
                    remote_assets[(asset.get("attributes", {}) or {}).get("fileName")] = asset

            ordered_ids: List[Optional[str]] = []
            for local in files:
                name = os.path.basename(local)
                checksum = assetlib.md5_of(local)
                asset = remote_assets.pop(name, None)
                if asset:
                    attributes = asset.get("attributes", {}) or {}
                    if (attributes.get("sourceFileChecksum") == checksum
                            and assetlib.is_complete(asset)):
                        plan.add(planner.NOOP, f"{set_path}/{name}", "unchanged")
                        ordered_ids.append(asset["id"])
                        continue
                    plan.add(planner.DELETE, f"{set_path}/{name}",
                             "changed or incomplete — will be replaced",
                             executed=not self.client.dry_run)
                    if not self.client.dry_run:
                        self.client.delete(f"/{spec.api_version}/{spec.api_type}/{asset['id']}")
                plan.add(planner.UPLOAD, f"{set_path}/{name}",
                         executed=not self.client.dry_run)
                if self.client.dry_run or set_id == NEW:
                    ordered_ids.append(None)
                    continue
                ordered_ids.append(assetlib.upload_asset(
                    self.client, spec.api_type, spec.api_version, spec.parent_rel,
                    spec.set_api_type, set_id, local, checksum))

            for name, asset in remote_assets.items():
                plan.add(planner.DELETE, f"{set_path}/{name}",
                         "no longer present locally", executed=not self.client.dry_run)
                if not self.client.dry_run:
                    self.client.delete(f"/{spec.api_version}/{spec.api_type}/{asset['id']}")

            if len(ordered_ids) > 1:
                plan.add(planner.ORDER, set_path, "order follows the filenames",
                         executed=not self.client.dry_run)
            if not self.client.dry_run and set_id != NEW and all(ordered_ids):
                self.client.patch(
                    f"/{spec.api_version}/{spec.set_api_type}/{set_id}/relationships/{spec.relationship}",
                    {"data": [{"type": spec.api_type, "id": i} for i in ordered_ids]})

        # Sets whose local directory is gone
        for display_type, entry in remote_sets.items():
            plan.add(planner.DELETE, f"{item_path}/{spec.name}/{display_type}",
                     "Ordner lokal entfernt", executed=not self.client.dry_run)
            if not self.client.dry_run:
                self.client.delete(f"/{spec.api_version}/{spec.set_api_type}/{entry['id']}")


# ---------------------------------------------------------------------------
def collection_items(item: Optional[dict], child: Resource) -> List[dict]:
    """Get a record's child collection in list form (dict or list)."""
    if not item:
        return []
    raw = item.get(child.doc_key())
    if raw is None:
        return []
    if child.keyed and isinstance(raw, dict):
        return differ.keyed_to_items(raw, child.key)
    if child.singleton and isinstance(raw, dict):
        return [raw]
    return list(raw)


def slugify(key: str) -> str:
    """A key in filename form: bundle prefix removed, the rest unchanged."""
    try:
        prefix = paths.load_app_config().get("idPrefix") or ""
    except SystemExit:
        prefix = ""
    if prefix and key.startswith(prefix):
        return key[len(prefix):]
    return key
