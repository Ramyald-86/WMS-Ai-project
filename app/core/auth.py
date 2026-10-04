import base64
import hashlib
import hmac
import json
import os
import secrets
import time


def _b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def hash_password(password: str, iterations: int = 310_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return f"pbkdf2_sha256${iterations}${_b64e(salt)}${_b64e(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        scheme, iterations_s, salt_s, digest_s = encoded.split("$", 3)
        if scheme != "pbkdf2_sha256":
            return False
        iterations = int(iterations_s)
        salt = _b64d(salt_s)
        expected = _b64d(digest_s)
        actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def create_session_token(username: str, secret_key: str, ttl_hours: int) -> str:
    payload = {
        "u": username,
        "exp": int(time.time()) + int(ttl_hours * 3600),
        "n": secrets.token_hex(8),
    }
    body = _b64e(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8"))
    sig = _b64e(hmac.new(secret_key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session_token(token: str | None, secret_key: str) -> dict | None:
    if not token or "." not in token:
        return None
    try:
        body, sig = token.rsplit(".", 1)
        expected = _b64e(hmac.new(secret_key.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        payload = json.loads(_b64d(body).decode("utf-8"))
        if int(payload.get("exp", 0)) < int(time.time()):
            return None
        if not payload.get("u"):
            return None
        return payload
    except Exception:
        return None
