"""ES256 JWT for the App Store Connect API, with renewal.

The TokenProvider mints a fresh token when needed. Apple caps a token's
lifetime at 20 minutes, and a full pull or push can take longer than that.

Credentials come from the environment:
  ASC_ISSUER_ID, ASC_KEY_ID, ASC_PRIVATE_KEY_PATH
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

try:
    import jwt  # PyJWT
except ImportError:  # only an error once the API is actually used
    jwt = None

TOKEN_LIFETIME = 1200      # 20 minutes, Apple's maximum
RENEW_BEFORE = 120         # renew 2 minutes early, for safety

ENV_VARS = ("ASC_ISSUER_ID", "ASC_KEY_ID", "ASC_PRIVATE_KEY_PATH")

# Named profiles, for people who work across several App Store Connect
# accounts. One file outside the repository, one block per account:
#
#     ~/.config/ascsync/credentials.json
#     {
#       "default": {"issuerId": "…", "keyId": "…",
#                   "privateKeyPath": "~/.appstoreconnect/private_keys/AuthKey_X.p8"},
#       "client-b": {"issuerId": "…", "keyId": "…", "privateKeyPath": "…"}
#     }
#
#     ascsync --profile client-b plan
#     ASCSYNC_PROFILE=client-b ascsync plan
#
# The environment always wins: a shell that has the three variables set keeps
# behaving exactly as before, profiles or not.
PROFILES_PATH = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config"),
    "ascsync", "credentials.json")
ENV_PROFILE = "ASCSYNC_PROFILE"


def load_profile(name: Optional[str] = None) -> dict:
    """The named profile's three values, or {} when there is nothing to load."""
    name = name or os.environ.get(ENV_PROFILE)
    if not os.path.exists(PROFILES_PATH):
        if name:
            raise MissingCredentials(
                f"No profile file at {PROFILES_PATH}, so '{name}' cannot be "
                f"resolved.")
        return {}
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            profiles = json.load(f)
    except ValueError as e:
        raise MissingCredentials(f"{PROFILES_PATH} is not valid JSON: {e}")
    chosen = name or "default"
    if chosen not in profiles:
        if name:
            raise MissingCredentials(
                f"No profile '{name}' in {PROFILES_PATH}. "
                f"Known: {', '.join(sorted(profiles)) or 'none'}")
        return {}
    entry = profiles[chosen] or {}
    return {"ASC_ISSUER_ID": entry.get("issuerId", ""),
            "ASC_KEY_ID": entry.get("keyId", ""),
            "ASC_PRIVATE_KEY_PATH": entry.get("privateKeyPath", "")}


def resolve(profile: Optional[str] = None) -> dict:
    """Environment first, profile second — never the other way round."""
    from_profile = load_profile(profile)
    return {name: os.environ.get(name) or from_profile.get(name, "")
            for name in ENV_VARS}


class MissingCredentials(RuntimeError):
    pass


def missing_env(profile: Optional[str] = None) -> list:
    values = resolve(profile)
    return [v for v in ENV_VARS if not values.get(v)]


class TokenProvider:
    """Hands out a valid bearer token and renews it on its own."""

    def __init__(self, profile: Optional[str] = None) -> None:
        values = resolve(profile)
        missing = [v for v in ENV_VARS if not values.get(v)]
        if missing:
            raise MissingCredentials(
                "Missing credentials: " + ", ".join(missing) +
                "\n  export ASC_ISSUER_ID=… ASC_KEY_ID=… ASC_PRIVATE_KEY_PATH=…"
                f"\n  or put a profile in {PROFILES_PATH} and pass --profile")
        self.issuer = values["ASC_ISSUER_ID"]
        self.key_id = values["ASC_KEY_ID"]
        self.key_path = os.path.expanduser(values["ASC_PRIVATE_KEY_PATH"])
        if not os.path.exists(self.key_path):
            raise MissingCredentials(f"Private key not found: {self.key_path}")
        self._token: Optional[str] = None
        self._expires_at = 0.0

    def token(self) -> str:
        if self._token and time.time() < self._expires_at - RENEW_BEFORE:
            return self._token
        if jwt is None:
            raise SystemExit("PyJWT is missing. Run: pip install -e . in the active venv")
        with open(self.key_path, "r") as f:
            private_key = f.read()
        now = int(time.time())
        payload = {"iss": self.issuer, "iat": now,
                   "exp": now + TOKEN_LIFETIME, "aud": "appstoreconnect-v1"}
        self._token = jwt.encode(payload, private_key, algorithm="ES256",
                                 headers={"kid": self.key_id})
        self._expires_at = now + TOKEN_LIFETIME
        return self._token
