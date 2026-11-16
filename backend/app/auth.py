"""Basic login: one shared account from the environment.

The browser gets a signed, HTTP-only session cookie. Scripts can send HTTP Basic
credentials on each request instead, which keeps the API easy to use with curl.
"""
import base64
import hashlib
import hmac
import secrets
import time

from fastapi import HTTPException, Request

from .config import settings

COOKIE = "session"
MAX_AGE = 12 * 3600


def check_credentials(username: str, password: str) -> bool:
    return (secrets.compare_digest(username.encode(), settings.app_username.encode())
            and secrets.compare_digest(password.encode(), settings.app_password.encode()))


def _sign(payload: str) -> str:
    return hmac.new(settings.secret_key.encode(), payload.encode(), hashlib.sha256).hexdigest()


def make_token(username: str) -> str:
    payload = f"{username}|{int(time.time()) + MAX_AGE}"
    return f"{payload}|{_sign(payload)}"


def _valid_token(token: str) -> bool:
    try:
        username, expires, signature = token.rsplit("|", 2)
    except ValueError:
        return False
    payload = f"{username}|{expires}"
    return hmac.compare_digest(signature, _sign(payload)) and int(expires) > time.time()


def require_login(request: Request) -> None:
    if _valid_token(request.cookies.get(COOKIE, "")):
        return
    header = request.headers.get("authorization", "")
    if header.lower().startswith("basic "):
        try:
            username, _, password = base64.b64decode(header[6:]).decode().partition(":")
        except Exception:
            username = password = ""
        if check_credentials(username, password):
            return
    raise HTTPException(status_code=401, detail="Login required.")
