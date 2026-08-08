"""HTTP client for the App Store Connect API.

Hardened with: token renewal (core.auth), the rate-limit header, an ETag cache
for GETs, a request log, and a dry run that reliably intercepts every write.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from . import cassette as cassettelib
from . import paths
from .auth import TokenProvider

try:
    import requests
except ImportError:  # only an error once the API is actually used
    requests = None

# Failure -> what to do about it. Matched against the raw response body, most
# specific first. Everything in this list cost somebody an hour once.
GUIDANCE = [
    ("is not an attribute on the resource",
     "The field does not exist on that resource. Fix the declaration in "
     "resources/, not the data — '.snapshot/' after a pull is the truth about "
     "which fields ASC knows."),
    ("is not a relationship on the resource",
     "Wrong relationship name in the declaration. Fetch the parent and read "
     "its 'relationships' keys to see what it is really called."),
    ("You must provide a value for the relationship",
     "A create is missing its parent relationship. Reading works over "
     "root_path without one, which is why this only ever shows up on the "
     "first genuine create."),
    ("is not a valid",
     "An enum value ASC does not accept. The exact spelling is in the error; "
     "correct the choices in the resource declaration."),
    ("already exists",
     "The push tried to create something that is already there — usually "
     "because it could not see it. A missing 'list_rel' on a child resource "
     "makes every existing record invisible."),
    ("The provided entity includes an attribute with a value that has already",
     "Something unique is taken: a locale twice, or a product id that exists "
     "elsewhere in the account."),
]

API = "https://api.appstoreconnect.apple.com"
RETRY_STATUS = (429, 500, 502, 503, 504)
MAX_ATTEMPTS = 5
# Transport errors (connection dropped, timeout, no ephemeral ports left) are
# treated like a 503; see _send.
TRANSPORT_ERRORS = ((requests.exceptions.ConnectionError,
                     requests.exceptions.Timeout) if requests else ())


class ApiError(RuntimeError):
    """An HTTP error with ASC's error objects already parsed out."""

    def __init__(self, method: str, path: str, status: int, body: str):
        self.method = method
        self.path = path
        self.status = status
        self.body = body
        self.details = self._parse(body)
        super().__init__(f"{method} {path} -> {status}: {self.summary()}")

    @staticmethod
    def _parse(body: str) -> list:
        try:
            return json.loads(body).get("errors", []) or []
        except Exception:
            return []

    def summary(self) -> str:
        if not self.details:
            return (self.body or "")[:400] + self.advice()
        out = []
        for e in self.details:
            out.append(" / ".join(x for x in (e.get("title"), e.get("detail"),
                                              (e.get("source") or {}).get("pointer")) if x))
        return " | ".join(out) + self.advice()

    def advice(self) -> str:
        """A sentence about what to do — for the failures that actually happen.

        Every entry here was paid for once. Apple's own message says what is
        wrong and almost never says where to look, and "where to look" is the
        difference between a two-minute fix and an afternoon.
        """
        for needle, hint in GUIDANCE:
            if self.mentions(needle):
                return f"\n      -> {hint}"
        if self.status == 401:
            return ("\n      -> The key was rejected. Check ASC_KEY_ID against the "
                    "filename, and that the key still exists in ASC.")
        if self.status == 403:
            return ("\n      -> Authenticated but not allowed. The key's role is "
                    "probably too low; content work needs App Manager.")
        if self.status == 429:
            return ("\n      -> Rate limited. 'ascsync doctor' shows what is left "
                    "of this hour's budget.")
        return ""

    def mentions(self, needle: str) -> bool:
        return needle.lower() in (self.body or "").lower()


class Client:
    def __init__(self, dry_run: bool = True, verbose: bool = False,
                 log_requests: bool = True, profile: Optional[str] = None) -> None:
        if requests is None:
            raise SystemExit("requests is missing. Run: pip install -e . in the active venv")
        self.dry_run = dry_run
        self.verbose = verbose
        self.log_requests = log_requests
        # Recorded traffic, if ASCSYNC_CASSETTE is set. Replaying needs no
        # credentials and never opens a socket — that is what lets CI exercise
        # pull and push without an API key.
        self.cassette = cassettelib.from_environment()
        replaying = bool(self.cassette) and self.cassette.mode != "record"
        self.tokens = None if replaying else TokenProvider(profile)
        self.session = requests.Session()
        # A push makes calls in the thousands. Without a bigger pool, requests
        # keeps opening new connections until the OS runs out of ephemeral
        # ports (OSError 49) — which is exactly what aborted the first run,
        # part way through the achievement images.
        adapter = requests.adapters.HTTPAdapter(pool_connections=4, pool_maxsize=8)
        self.session.mount("https://", adapter)
        self._etags: dict = {}
        self._cache: dict = {}
        self.calls = 0
        self.rate_remaining: Optional[int] = None

    # -- intern ------------------------------------------------------------
    def _url(self, path: str) -> str:
        return path if path.startswith("http") else f"{API}{path}"

    def _headers(self) -> dict:
        return {"Authorization": f"Bearer {self.tokens.token()}"}

    def _note_rate_limit(self, response) -> None:
        raw = response.headers.get("X-Rate-Limit", "")
        for part in raw.split(";"):
            if part.startswith("user-hour-rem:"):
                try:
                    self.rate_remaining = int(part.split(":", 1)[1])
                except ValueError:
                    pass
        if self.rate_remaining is not None and self.rate_remaining < 50:
            print(f"  [rate] only {self.rate_remaining} calls left this hour — pausing 2s")
            time.sleep(2)

    def _log(self, method: str, path: str, status: int, seconds: float) -> None:
        if not self.log_requests:
            return
        try:
            with open(paths.REQUEST_LOG_PATH, "a", encoding="utf-8") as f:
                f.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
                                    "method": method, "path": path,
                                    "status": status, "ms": int(seconds * 1000)}) + "\n")
        except OSError:
            pass

    def _send(self, method: str, path: str, **kw):
        url = self._url(path)
        if self.cassette and self.cassette.mode != "record":
            found = self.cassette.find(method, path, kw.get("params"))
            if found is None:
                raise ApiError(method, path, 599,
                               '{"errors":[{"title":"not in cassette",'
                               '"detail":"record it or fix the request"}]}')
            self.calls += 1
            return cassettelib.Replayed(*found)
        last = None
        for attempt in range(MAX_ATTEMPTS):
            headers = dict(self._headers())
            headers.update(kw.pop("headers", {}) if attempt == 0 else {})
            started = time.time()
            try:
                last = self.session.request(method, url, headers=headers, **kw)
            except TRANSPORT_ERRORS as e:
                # A dropped connection or timeout is not an HTTP status and
                # used to propagate straight out — in the middle of a push that
                # would leave ASC half written.
                if attempt == MAX_ATTEMPTS - 1:
                    raise
                wait = 2 ** attempt
                print(f"  [retry] {method} {path} -> {type(e).__name__}, retrying in {wait}s "
                      f"({attempt + 1}/{MAX_ATTEMPTS - 1})")
                time.sleep(wait)
                continue
            self.calls += 1
            self._note_rate_limit(last)
            if self.cassette and self.cassette.mode == "record":
                self.cassette.record(method, path, kw.get("params"),
                                     last.status_code, last.text)
            self._log(method, path, last.status_code, time.time() - started)
            if self.verbose:
                print(f"  [http] {method} {path} -> {last.status_code}")
            if last.status_code in RETRY_STATUS and attempt < MAX_ATTEMPTS - 1:
                wait = 2 ** attempt
                print(f"  [retry] {method} {path} -> {last.status_code}, retrying in {wait}s "
                      f"({attempt + 1}/{MAX_ATTEMPTS - 1})")
                time.sleep(wait)
                continue
            return last
        return last

    # -- lesend ------------------------------------------------------------
    def get(self, path: str, **params) -> dict:
        url = self._url(path)
        headers = {}
        etag = self._etags.get(url) if not params else None
        if etag:
            headers["If-None-Match"] = etag
        r = self._send("GET", url, params=params or None, headers=headers)
        if r.status_code == 304 and url in self._cache:
            return self._cache[url]
        if not r.ok:
            raise ApiError("GET", path, r.status_code, r.text)
        payload = r.json()
        if not params and r.headers.get("ETag"):
            self._etags[url] = r.headers["ETag"]
            self._cache[url] = payload
        return payload

    def get_optional(self, path: str, **params) -> Optional[dict]:
        """Like get(), but returns None on a 404 or an empty relationship."""
        try:
            payload = self.get(path, **params)
        except ApiError as e:
            if e.status in (404, 403):
                return None
            raise
        return payload.get("data")

    def get_all(self, path: str, **params) -> list:
        """Follows pagination and collects every data record."""
        out: list = []
        url, p = path, dict(params)
        p.setdefault("limit", 200)
        while url:
            payload = self.get(url, **p) if p else self.get(url)
            out.extend(payload.get("data", []))
            url = (payload.get("links") or {}).get("next", "")
            p = {}  # the query is already part of the next URL
        return out

    # -- writing -----------------------------------------------------------
    def post(self, path: str, body: dict) -> Optional[dict]:
        if self.dry_run:
            print(f"      [dry-run] POST {path}")
            return None
        r = self._send("POST", path, json=body)
        if not r.ok:
            raise ApiError("POST", path, r.status_code, r.text)
        return r.json()

    def patch(self, path: str, body: dict) -> Optional[dict]:
        if self.dry_run:
            print(f"      [dry-run] PATCH {path}")
            return None
        r = self._send("PATCH", path, json=body)
        if not r.ok:
            raise ApiError("PATCH", path, r.status_code, r.text)
        return r.json() if r.text else None

    def delete(self, path: str) -> None:
        if self.dry_run:
            print(f"      [dry-run] DELETE {path}")
            return
        r = self._send("DELETE", path)
        if not r.ok and r.status_code != 404:
            raise ApiError("DELETE", path, r.status_code, r.text)

    def upload(self, operation: dict, data: bytes) -> None:
        """Send one uploadOperation chunk to the URL Apple handed us."""
        headers = {h["name"]: h["value"] for h in operation.get("requestHeaders", [])}
        chunk = data[operation["offset"]: operation["offset"] + operation["length"]]
        r = requests.request(operation["method"], operation["url"],
                             headers=headers, data=chunk)
        if not r.ok:
            raise ApiError(operation["method"], operation["url"], r.status_code, r.text)


def resolve_app_id(client: Client, bundle_id: str) -> str:
    apps = client.get_all("/v1/apps", **{"filter[bundleId]": bundle_id})
    if not apps:
        raise SystemExit(f"No app with bundleId={bundle_id} found in ASC.")
    return apps[0]["id"]


def state_of(item: dict) -> str:
    """A resource's state field — Apple names it differently per type."""
    at = item.get("attributes", {}) or {}
    for key in ("state", "appStoreState", "appVersionState", "eventState"):
        if at.get(key):
            return at[key]
    return ""


def env_summary() -> str:
    parts = []
    for var in ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY_PATH"):
        value = os.environ.get(var)
        parts.append(f"{var}={'gesetzt' if value else 'FEHLT'}")
    return ", ".join(parts)
