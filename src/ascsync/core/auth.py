"""ES256 JWT for the App Store Connect API, with renewal.

The TokenProvider mints a fresh token when needed. Apple caps a token's
lifetime at 20 minutes, and a full pull or push can take longer than that.

Credentials come from the environment:
  ASC_ISSUER_ID, ASC_KEY_ID, ASC_PRIVATE_KEY_PATH
"""
from __future__ import annotations

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


class MissingCredentials(RuntimeError):
    pass


def missing_env() -> list:
    return [v for v in ENV_VARS if not os.environ.get(v)]


class TokenProvider:
    """Hands out a valid bearer token and renews it on its own."""

    def __init__(self) -> None:
        missing = missing_env()
        if missing:
            raise MissingCredentials(
                "Missing environment variables: " + ", ".join(missing) +
                "\n  export ASC_ISSUER_ID=… ASC_KEY_ID=… ASC_PRIVATE_KEY_PATH=…")
        self.issuer = os.environ["ASC_ISSUER_ID"]
        self.key_id = os.environ["ASC_KEY_ID"]
        self.key_path = os.path.expanduser(os.environ["ASC_PRIVATE_KEY_PATH"])
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
