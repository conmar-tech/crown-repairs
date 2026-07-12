"""Google Sign-In and signed application sessions."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from dataclasses import dataclass

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from google.auth.transport import requests as google_requests
from google.oauth2 import id_token


DEFAULT_ALLOWED_EMAILS = (
    "shop@crownjewelryrepair.com,serg@crownjewelryrepair.com"
)


def _env_bool(name: str, default: bool = False) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AuthSettings:
    google_client_id: str
    session_secret: str
    allowed_emails: frozenset[str]
    auth_disabled: bool
    session_hours: int = 12

    @classmethod
    def from_env(cls) -> "AuthSettings":
        allowed = frozenset(
            value.strip().lower()
            for value in os.getenv("ALLOWED_EMAILS", DEFAULT_ALLOWED_EMAILS).split(",")
            if value.strip()
        )
        return cls(
            google_client_id=os.getenv("GOOGLE_CLIENT_ID", "").strip(),
            session_secret=os.getenv("SESSION_SECRET", "").strip(),
            allowed_emails=allowed,
            auth_disabled=_env_bool("AUTH_DISABLED"),
            session_hours=int(os.getenv("SESSION_HOURS", "12")),
        )


def _b64encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _b64decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class SessionManager:
    def __init__(self, settings: AuthSettings):
        self.settings = settings
        if not settings.auth_disabled and not settings.session_secret:
            raise RuntimeError("SESSION_SECRET is required when authentication is enabled")

    def create(self, email: str, name: str) -> str:
        payload = {
            "email": email.lower(),
            "name": name,
            "exp": int(time.time()) + self.settings.session_hours * 3600,
        }
        encoded = _b64encode(
            json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        )
        signature = hmac.new(
            self.settings.session_secret.encode("utf-8"),
            encoded.encode("ascii"),
            hashlib.sha256,
        ).digest()
        return f"{encoded}.{_b64encode(signature)}"

    def verify(self, token: str) -> dict[str, object]:
        try:
            encoded, supplied_signature = token.split(".", 1)
            expected_signature = hmac.new(
                self.settings.session_secret.encode("utf-8"),
                encoded.encode("ascii"),
                hashlib.sha256,
            ).digest()
            if not hmac.compare_digest(expected_signature, _b64decode(supplied_signature)):
                raise ValueError("invalid signature")
            payload = json.loads(_b64decode(encoded))
            email = str(payload["email"]).lower()
            if int(payload["exp"]) < int(time.time()):
                raise ValueError("session expired")
            if email not in self.settings.allowed_emails:
                raise ValueError("email is not allowed")
            return payload
        except (KeyError, ValueError, TypeError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED) from exc


def install_auth_middleware(app, settings: AuthSettings, sessions: SessionManager) -> None:
    @app.middleware("http")
    async def authentication(request: Request, call_next):
        path = request.url.path
        is_public = (
            path == "/health"
            or path == "/login"
            or path == "/auth/callback"
            or path.startswith("/static/")
        )

        if settings.auth_disabled:
            request.state.user_email = next(iter(settings.allowed_emails), "developer@local")
            request.state.user_name = "Local development"
            return await call_next(request)

        if is_public:
            return await call_next(request)

        token = request.cookies.get("repairs_session", "")
        try:
            session = sessions.verify(token)
            request.state.user_email = session["email"]
            request.state.user_name = session.get("name") or session["email"]
        except HTTPException:
            if path.startswith("/api/"):
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
            return RedirectResponse("/login", status_code=303)
        return await call_next(request)


def verify_google_credential(credential: str, settings: AuthSettings) -> tuple[str, str]:
    if not settings.google_client_id:
        raise HTTPException(status_code=503, detail="Google Sign-In is not configured")
    try:
        info = id_token.verify_oauth2_token(
            credential,
            google_requests.Request(),
            settings.google_client_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Invalid Google credential") from exc

    email = str(info.get("email", "")).lower()
    if not info.get("email_verified") or email not in settings.allowed_emails:
        raise HTTPException(status_code=403, detail="This Google account is not authorized")
    return email, str(info.get("name") or email)
