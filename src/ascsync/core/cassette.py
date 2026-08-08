"""Recorded API traffic, so the tests can see what the API actually answers.

The self-test covers the parts that are tricky to reason about. It has never
seen App Store Connect, and that is precisely where this project lost a day:
the first real push failed six times in a row on things no offline test could
have caught — a field that does not exist on a resource, a relationship the API
names differently, an enum value spelled almost right, a parent relationship
that reading never needs and creating always does.

A cassette closes that gap. Record once against a real account, replay for
ever after, in CI, with no credentials:

    ASCSYNC_CASSETTE=tests/cassettes/pull.json ASCSYNC_CASSETTE_MODE=record \\
        ascsync pull --snapshot-only

    ASCSYNC_CASSETTE=tests/cassettes/pull.json ascsync pull --snapshot-only

WHAT IS RECORDED, AND WHAT IS DELIBERATELY THROWN AWAY

Structure, ids, relationships, enum values, field NAMES — everything that
decides whether a request is well formed.

Not your content. Every free-text value is replaced with a synthetic one before
anything is written to disk (see `scrub`). Descriptions, keywords, review
notes, contact details, URLs: gone. What survives is the shape.

That is a real limitation and worth stating plainly: these fixtures prove that
a request is built correctly, not that your copy is right. A cassette will
never catch a typo in your app description. It will catch the class of bug that
actually hurt.
"""
from __future__ import annotations

import atexit
import json
import os
import re
from typing import Any, Dict, List, Optional, Tuple

ENV_PATH = "ASCSYNC_CASSETTE"
ENV_MODE = "ASCSYNC_CASSETTE_MODE"       # record | replay (default: replay)

# What the recorder renames the real app to, so a cassette can be replayed by
# anyone with the shipped scaffold.
PLACEHOLDER_BUNDLE = "com.example.app"
PLACEHOLDER_APP_ID = "1234567890"

# Values that carry meaning to the API and must survive scrubbing.
_ENUM = re.compile(r"^[A-Z][A-Z0-9_]{2,}$")          # DRAFT, NO_COST_ASSOCIATED
_LOCALE = re.compile(r"^[a-z]{2}(-[A-Za-z]{2,4})?$")  # en-US, de-DE
_UUID = re.compile(r"^[0-9a-fA-F-]{16,}$")
_DIGITS = re.compile(r"^\d+$")
_DURATION = re.compile(r"^P(T)?\d+[DHMSWY]")          # P7D, PT168H
_RRULE = re.compile(r"^FREQ=")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_TERRITORY = re.compile(r"^[A-Z]{3}$")

# Keys whose values are structural even when they look like prose.
_KEEP_KEYS = {"type", "id", "locale", "state", "appStoreState", "eventState",
              "appVersionState", "platform", "deviceFamily", "territory",
              "next", "self", "related"}


def _looks_structural(value: str) -> bool:
    return bool(_ENUM.match(value) or _LOCALE.match(value) or _UUID.match(value)
                or _DIGITS.match(value) or _DURATION.match(value)
                or _RRULE.match(value) or _DATE.match(value)
                or _TERRITORY.match(value))


def scrub(node: Any, key: str = "") -> Any:
    """Replace free text with synthetic text; keep everything structural.

    Deliberately aggressive: when in doubt, throw the value away. A fixture
    that is missing a nuance is a nuisance; a fixture that leaks somebody's
    unreleased marketing copy is a incident.
    """
    if isinstance(node, dict):
        return {k: scrub(v, k) for k, v in node.items()}
    if isinstance(node, list):
        return [scrub(v, key) for v in node]
    if isinstance(node, str):
        if key in _KEEP_KEYS or _looks_structural(node):
            return node
        if node.startswith(("http://", "https://")):
            return "https://example.com/"
        if not node:
            return node
        return f"text-{len(node)}"
    return node


def _signature(method: str, path: str, params: Optional[dict]) -> str:
    """What makes two requests the same for replay purposes.

    Query parameters are part of it — `limit` and `include` change the answer,
    and a cassette that ignored them would replay the wrong body.
    """
    query = "&".join(f"{k}={v}" for k, v in sorted((params or {}).items()))
    return f"{method} {path}" + (f"?{query}" if query else "")


class Cassette:
    def __init__(self, path: str, mode: str = "replay"):
        self.path = path
        self.mode = mode
        self.entries: List[dict] = []
        self.requests: List[dict] = []
        self.unmatched: List[str] = []
        if mode != "record" and os.path.exists(path):
            loaded = json.loads(open(path, encoding="utf-8").read())
            self.entries = loaded["entries"]
            self.requests = loaded.get("requests") or []

    # -- recording ---------------------------------------------------------
    def record_request(self, method: str, path: str, body: Optional[dict]) -> None:
        """What the tool would SEND, captured during a dry run.

        Recording responses proves the tool can read. This proves it builds a
        well-formed request — which is the half that broke six times on the
        first real push, and the half no read-only recording can reach.
        """
        self.requests.append({"method": method, "path": path,
                              "body": scrub(body) if body is not None else None})

    def record(self, method: str, path: str, params: Optional[dict],
               status: int, body: str) -> None:
        try:
            parsed = json.loads(body) if body else None
        except ValueError:
            parsed = None
        self.entries.append({
            "signature": _signature(method, path, params),
            "status": status,
            "body": scrub(parsed) if parsed is not None else None,
        })

    # -- anonymising -------------------------------------------------------
    def aliases(self) -> Dict[str, str]:
        """Which real identifiers to rename, and to what.

        Scrubbing bodies is not enough: the bundle id and the numeric app id
        also sit in every URL, and the bundle id is the prefix of every
        achievement and product id. Renaming them makes the cassette belong to
        `com.example.app` rather than to whoever recorded it — which is both a
        privacy matter and what lets anyone replay it.
        """
        from . import paths
        out: Dict[str, str] = {}
        try:
            bundle = (paths.load_app_config().get("bundleId") or "").strip()
        except SystemExit:
            bundle = ""
        if bundle:
            out[bundle] = PLACEHOLDER_BUNDLE
        for entry in self.entries:
            if "filter[bundleId]" not in entry["signature"]:
                continue
            data = ((entry.get("body") or {}).get("data") or [])
            if data and data[0].get("id"):
                out[str(data[0]["id"])] = PLACEHOLDER_APP_ID
            break
        return out

    def save(self) -> str:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = {"entries": self.entries}
        if self.requests:
            payload["requests"] = self.requests
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        for real, alias in self.aliases().items():
            text = text.replace(real, alias)
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text + "\n")
        return self.path

    # -- replay ------------------------------------------------------------
    def find(self, method: str, path: str,
             params: Optional[dict]) -> Optional[Tuple[int, Any]]:
        wanted = _signature(method, path, params)
        for entry in self.entries:
            if entry["signature"] == wanted:
                return entry["status"], entry["body"]
        self.unmatched.append(wanted)
        return None


class Replayed:
    """Just enough of a requests.Response for the client to work with."""

    def __init__(self, status: int, body: Any):
        self.status_code = status
        self._body = body
        self.headers: Dict[str, str] = {}
        self.text = json.dumps(body, ensure_ascii=False) if body is not None else ""

    @property
    def ok(self) -> bool:
        return self.status_code < 400

    def json(self) -> Any:
        return self._body


def from_environment() -> Optional[Cassette]:
    """The cassette for this process, or None when the variable is unset.

    Recording saves on exit rather than after each call: a run that dies
    half way through still leaves a usable cassette of what it managed, and
    a successful run does not pay for one write per request.
    """
    path = os.environ.get(ENV_PATH)
    if not path:
        return None
    tape = Cassette(path, os.environ.get(ENV_MODE) or "replay")
    if tape.mode == "record":
        atexit.register(lambda: (tape.entries or tape.requests) and tape.save())
    return tape
