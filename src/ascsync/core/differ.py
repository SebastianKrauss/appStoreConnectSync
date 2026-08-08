"""Three-way diff: data/ (desired) x .snapshot/ (as of the last pull) x ASC (remote).

The purpose is to write only where it is certain that nobody else's work gets
run over. Anyone who edited in ASC by hand shows up as 'drift' instead of
being silently overwritten.

    desired = snapshot = remote   -> ok
    desired != snapshot, remote = snapshot -> write
    desired = snapshot, remote != snapshot -> drift
    desired != snapshot, remote != snapshot, desired = remote -> ok (converged)
    desired != snapshot, remote != snapshot, desired != remote -> conflict
    no snapshot (first run) -> write, but say so
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple

WRITE, OK, DRIFT, CONFLICT = "write", "ok", "drift", "conflict"


def _norm(value: Any) -> Any:
    """Comparable form: ASC treats None and '' alike, and whitespace is noise."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    return value


def classify_field(desired: Any, snapshot: Any, remote: Any,
                   have_snapshot: bool) -> str:
    d, s, r = _norm(desired), _norm(snapshot), _norm(remote)
    if d == r:
        return OK
    if not have_snapshot:
        return WRITE
    if d == s:                 # unchanged locally, ASC differs
        return DRIFT
    if r == s:                 # changed locally only
        return WRITE
    return CONFLICT            # both sides changed


def diff_attributes(desired: Dict[str, Any], snapshot: Optional[Dict[str, Any]],
                    remote: Dict[str, Any], fields) -> Tuple[dict, dict, dict]:
    """Returns (to_write, drift, conflict), each keyed by field name.

    `fields` is the resource's writable dict; depending on the field, an empty
    desired value means "do not touch" (skip_if_empty).
    """
    have_snapshot = snapshot is not None
    snapshot = snapshot or {}
    to_write: dict = {}
    drift: dict = {}
    conflict: dict = {}
    for name, spec in fields.items():
        if name not in desired:
            continue
        value = desired.get(name)
        if spec.skip_if_empty and (value is None or value == ""):
            continue
        verdict = classify_field(value, snapshot.get(name), remote.get(name),
                                 have_snapshot)
        if verdict == WRITE:
            to_write[name] = value
        elif verdict == DRIFT:
            drift[name] = (remote.get(name), snapshot.get(name))
        elif verdict == CONFLICT:
            conflict[name] = (value, remote.get(name))
    return to_write, drift, conflict


def index_by_key(items, key: str) -> Dict[str, dict]:
    """List of records -> dict keyed by the natural key."""
    out: Dict[str, dict] = {}
    for item in items or []:
        value = item.get(key)
        if value is None:
            continue
        out[str(value)] = item
    return out


def keyed_to_items(mapping: Optional[dict], key: str) -> list:
    """Turn a keyed collection (locale -> {...}) into list form."""
    out = []
    for k, value in (mapping or {}).items():
        item = dict(value or {})
        item[key] = k
        out.append(item)
    return out


def items_to_keyed(items, key: str) -> dict:
    out = {}
    for item in items or []:
        k = item.get(key)
        if k is None:
            continue
        rest = {kk: vv for kk, vv in item.items() if kk != key}
        out[str(k)] = rest
    return out
