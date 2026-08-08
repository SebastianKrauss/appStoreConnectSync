"""Assets: find them, check them offline, upload them, remember them.

Apple's upload is always three steps: reserve (POST) -> send the parts by PUT
to the URLs it names -> commit (PATCH uploaded=true [+ sourceFileChecksum]).
Incomplete assets are detected and replaced, so an aborted run can simply be
repeated.

Idempotence:
  - Where ASC knows `sourceFileChecksum` (screenshots, previews, event assets):
    the local file's MD5 is the comparison key.
  - Where it does not (Game Center images only have `uploaded`):
    assets.lock.json maps path -> md5 -> remote id.
"""
from __future__ import annotations

import hashlib
import os
import struct
from typing import Optional, Tuple

from . import paths
from .registry import ImageRule

COMPLETE_STATES = ("UPLOAD_COMPLETE", "COMPLETE")
VIDEO_EXTENSIONS = (".mov", ".m4v", ".mp4")


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------
def md5_of(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_asset(template: str, fallbacks=(), **fmt) -> Optional[str]:
    """The first existing hit from the main template and its fallbacks.

    This is what allows events to resolve <metric>-<play mode> -> <metric> ->
    default, and a video in place of an image (same name, different suffix).
    """
    for candidate in (template,) + tuple(fallbacks):
        try:
            rel = candidate.format(**fmt)
        except KeyError:
            continue
        for path in _with_media_extensions(paths.asset_path(rel)):
            if os.path.exists(path):
                return path
    return None


def _with_media_extensions(path: str):
    """The image path plus the allowed video variants of the same name."""
    yield path
    base, ext = os.path.splitext(path)
    if ext.lower() in (".png", ".jpg", ".jpeg"):
        for other in (".jpg", ".jpeg", ".png") + VIDEO_EXTENSIONS:
            if other != ext.lower():
                yield base + other


def ordered_files(directory: str, extensions) -> list:
    """A set's files, sorted — the order is the alphabetical filename order."""
    if not os.path.isdir(directory):
        return []
    names = sorted(n for n in os.listdir(directory)
                   if n.lower().endswith(tuple(extensions)) and not n.startswith("."))
    return [os.path.join(directory, n) for n in names]


# ---------------------------------------------------------------------------
# Offline checks
# ---------------------------------------------------------------------------
def image_size(path: str) -> Optional[Tuple[int, int, bool]]:
    """(width, height, has_alpha) without a third-party library; None for
    videos and anything unrecognised."""
    ext = os.path.splitext(path)[1].lower()
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                width, height = struct.unpack(">II", head[16:24])
                color_type = head[25]
                return width, height, color_type in (4, 6)
            if head[:2] == b"\xff\xd8":  # JPEG: SOF-Marker suchen
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    length = struct.unpack(">H", f.read(2))[0]
                    if 0xC0 <= marker[1] <= 0xCF and marker[1] not in (0xC4, 0xC8, 0xCC):
                        f.read(1)
                        height, width = struct.unpack(">HH", f.read(4))
                        return width, height, False
                    f.seek(length - 2, os.SEEK_CUR)
    except (OSError, struct.error):
        return None
    if ext in VIDEO_EXTENSIONS:
        return None
    return None


def check_file(path: str, rule: Optional[ImageRule]) -> list:
    """A list of complaints (empty means fine)."""
    problems = []
    name = paths.rel_to_asc(path)
    if not os.path.exists(path):
        return [f"{name}: file missing"]
    if os.path.getsize(path) == 0:
        return [f"{name}: file is empty"]
    if rule is None:
        return problems
    ext = os.path.splitext(path)[1].lower()
    allowed = tuple(rule.formats) + VIDEO_EXTENSIONS
    if ext not in allowed:
        problems.append(f"{name}: format {ext} not allowed ({', '.join(allowed)})")
    if ext in VIDEO_EXTENSIONS:
        return problems  # video dimensions need ffprobe — see validate.py
    size = image_size(path)
    if size is None:
        problems.append(f"{name}: cannot read image dimensions")
        return problems
    width, height, has_alpha = size
    if rule.exact and (width, height) not in rule.exact:
        allowed_sizes = ", ".join(f"{w}x{h}" for w, h in rule.exact)
        problems.append(f"{name}: {width}x{height} — allowed: {allowed_sizes}")
    if rule.min_width and width < rule.min_width:
        problems.append(f"{name}: width {width} < {rule.min_width}")
    if rule.min_height and height < rule.min_height:
        problems.append(f"{name}: height {height} < {rule.min_height}")
    if rule.max_width and width > rule.max_width:
        problems.append(f"{name}: width {width} > {rule.max_width}")
    if rule.max_height and height > rule.max_height:
        problems.append(f"{name}: height {height} > {rule.max_height}")
    if rule.aspect:
        actual = width / float(height)
        if abs(actual - rule.aspect) > rule.aspect_tolerance:
            problems.append(f"{name}: aspect ratio {actual:.3f}, expected {rule.aspect:.3f}")
    if has_alpha and not rule.allow_alpha:
        problems.append(f"{name}: alpha channel — Apple requires opaque images")
    return problems


# ---------------------------------------------------------------------------
# Lock file (for assets without sourceFileChecksum)
# ---------------------------------------------------------------------------
class AssetLock:
    def __init__(self, path: str = paths.LOCK_PATH):
        self.path = path
        self.data = paths.read_json(path, default={"assets": {}}).get("assets", {})
        self.dirty = False

    @staticmethod
    def _ids(entry: dict) -> list:
        """Old entries held a single remoteId, new ones hold a list."""
        if not entry:
            return []
        if entry.get("remoteIds"):
            return list(entry["remoteIds"])
        return [entry["remoteId"]] if entry.get("remoteId") else []

    def matches(self, key: str, checksum: str, remote_id: str) -> bool:
        entry = self.data.get(key)
        return bool(entry and entry.get("md5") == checksum
                    and remote_id in self._ids(entry))

    def remember(self, key: str, checksum: str, remote_id: str) -> None:
        # One file can hang in several places: the same event image belongs to
        # every occurrence of that variant, the same icon to every language.
        # Each place is a separate asset in ASC. Remembering only one id made
        # all the others look changed on every run, so they were re-uploaded.
        entry = self.data.get(key)
        ids = self._ids(entry) if entry and entry.get("md5") == checksum else []
        if remote_id not in ids:
            ids.append(remote_id)
        self.data[key] = {"md5": checksum, "remoteIds": sorted(ids)}
        self.dirty = True

    def forget(self, key: str) -> None:
        if key in self.data:
            del self.data[key]
            self.dirty = True

    def save(self) -> None:
        if self.dirty:
            paths.write_json(self.path, {"assets": dict(sorted(self.data.items()))})
            self.dirty = False


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------
def asset_state(item: dict) -> str:
    delivery = (item.get("attributes", {}) or {}).get("assetDeliveryState") or {}
    return delivery.get("state", "") or ""


def is_complete(item: dict) -> bool:
    return asset_state(item) in COMPLETE_STATES


def is_video(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in VIDEO_EXTENSIONS


def upload_asset(client, api_type: str, api_version: str, parent_rel: str,
                 parent_type: str, parent_id: str, path: str,
                 checksum: Optional[str] = None,
                 extra_attributes: Optional[dict] = None) -> Optional[str]:
    """Reserve -> PUT -> commit. Returns the remote id (None on a dry run)."""
    with open(path, "rb") as f:
        data = f.read()
    file_name = os.path.basename(path)
    reserved = client.post(f"/{api_version}/{api_type}", {
        "data": {
            "type": api_type,
            "attributes": {"fileName": file_name, "fileSize": len(data),
                           **(extra_attributes or {})},
            "relationships": {parent_rel: {"data": {"type": parent_type, "id": parent_id}}},
        }
    })
    if reserved is None:      # dry run
        return None
    asset = reserved["data"]
    for operation in asset["attributes"].get("uploadOperations") or []:
        client.upload(operation, data)
    attributes = {"uploaded": True}
    if checksum is not None:
        attributes["sourceFileChecksum"] = checksum
    client.patch(f"/{api_version}/{api_type}/{asset['id']}",
                 {"data": {"type": api_type, "id": asset["id"], "attributes": attributes}})
    return asset["id"]
