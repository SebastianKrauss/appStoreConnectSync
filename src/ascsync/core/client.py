"""HTTP client for the App Store Connect API.

Hardened with: token renewal (core.auth), the rate-limit header, an ETag cache
for GETs, a request log, and a dry run that reliably intercepts every write.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

from . import paths
from .auth import TokenProvider

try:
    import requests
except ImportError:  # only an error once the API is actually used
    requests = None

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
            return (self.body or "")[:400]
        out = []
        for e in self.details:
            out.append(" / ".join(x for x in (e.get("title"), e.get("detail"),
                                              (e.get("source") or {}).get("pointer")) if x))
        return " | ".join(out)

    def mentions(self, needle: str) -> bool:
        return needle.lower() in (self.body or "").lower()


class Client:
    def __init__(self, dry_run: bool = True, verbose: bool = False,
                 log_requests: bool = True) -> None:
        if requests is None:
            raise SystemExit("requests is missing. Run: pip install -e . in the active venv")
        self.dry_run = dry_run
        self.verbose = verbose
        self.log_requests = log_requests
        self.tokens = TokenProvider()
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
