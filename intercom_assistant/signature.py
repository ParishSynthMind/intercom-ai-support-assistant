"""Intercom webhook signatures.

Intercom signs every notification with an HMAC-SHA1 of the request body, keyed
with the app's client_secret, and sends it as `X-Hub-Signature: sha1=<hex>`.
We check it over the RAW bytes (before any JSON parsing) with a constant-time
comparison, and reject anything unsigned or malformed.
"""

from __future__ import annotations

import hashlib
import hmac

HEADER = "X-Hub-Signature"


def sign(body: bytes, client_secret: str) -> str:
    digest = hmac.new(client_secret.encode("utf-8"), body, hashlib.sha1).hexdigest()
    return f"sha1={digest}"


def verify(body: bytes, header_value: str | None, client_secret: str) -> bool:
    if not client_secret or not header_value:
        return False
    header_value = header_value.strip()
    if not header_value.startswith("sha1="):
        return False
    given = header_value[5:].lower()
    if len(given) != 40 or any(c not in "0123456789abcdef" for c in given):
        return False
    return hmac.compare_digest(sign(body, client_secret)[5:], given)
