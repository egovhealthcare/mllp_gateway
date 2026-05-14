"""JWT-based mutual authentication between the gateway and CARE.

The gateway signs outbound requests with its RSA private key.
Inbound requests from CARE are verified against CARE’s public key,
which is fetched (and cached) from CARE’s JWKS endpoint.
"""

import json
import logging
import threading
import time
import urllib.request

from joserfc import jwt
from joserfc.errors import BadSignatureError, DecodeError
from joserfc.jwk import RSAKey

logger = logging.getLogger(__name__)


class Auth:
    """Handles JWT signing (gateway → CARE) and verification (CARE → gateway).

    CARE’s public key is lazily fetched on first token verification and
    refreshed automatically if signature validation fails (key rotation).
    """

    def __init__(self, private_key_pem: bytes, care_api_url: str):
        self._key = RSAKey.import_key(private_key_pem)
        self._care_api_url = care_api_url
        self._care_public_key: RSAKey | None = None
        self._key_lock = threading.Lock()

    def generate_jwt(self, claims: dict | None = None, exp: int = 60) -> str:
        now = int(time.time())
        payload = {"iat": now, "exp": now + exp, **(claims or {})}
        return jwt.encode({"alg": "RS256"}, payload, self._key)

    def get_public_jwks(self) -> dict:
        return {"keys": [self._key.as_dict(private=False)]}

    def verify_care_token(self, token: str) -> dict:
        with self._key_lock:
            if self._care_public_key is None:
                self._refresh_care_public_key()
        try:
            return jwt.decode(token, self._care_public_key, algorithms=["RS256"]).claims
        except (BadSignatureError, DecodeError):
            # Key may have rotated — refresh and retry once
            with self._key_lock:
                self._care_public_key = None
                self._refresh_care_public_key()
            return jwt.decode(token, self._care_public_key, algorithms=["RS256"]).claims

    def _refresh_care_public_key(self) -> None:
        url = f"{self._care_api_url}/api/gateway_device/jwks.json/"
        try:
            req = urllib.request.Request(url, method="GET")
            req.add_header("Accept", "application/json")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
        except (urllib.error.URLError, OSError) as exc:
            raise RuntimeError(f"Failed to fetch CARE public key from {url}") from exc

        try:
            keys = data["keys"]
            if not keys:
                raise KeyError("empty keys list")
            self._care_public_key = RSAKey.import_key(keys[0])
        except (KeyError, IndexError, ValueError) as exc:
            raise RuntimeError(
                'Invalid JWKS response: expected {"keys": [...]}'
            ) from exc
