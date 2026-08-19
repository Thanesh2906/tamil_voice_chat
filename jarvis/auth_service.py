from __future__ import annotations

import time
import uuid

from .config import Settings
from .database import Database
from .security import (
    decode_token,
    encode_token,
    hash_password,
    new_claims,
    token_fingerprint,
    verify_password,
)


class AuthError(ValueError):
    pass


class AuthService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    def register(self, email: str, password: str, tenant_name: str) -> dict[str, str]:
        if len(password) < 12:
            raise AuthError("password must contain at least 12 characters")
        tenant_id, user_id = uuid.uuid4().hex, uuid.uuid4().hex
        self.db.execute("INSERT INTO tenants(id,name) VALUES(?,?)", (tenant_id, tenant_name))
        try:
            self.db.execute(
                "INSERT INTO users(id,tenant_id,email,password_hash) VALUES(?,?,?,?)",
                (user_id, tenant_id, email.lower().strip(), hash_password(password)),
            )
        except Exception as exc:
            raise AuthError("email is already registered") from exc
        self.db.audit(tenant_id, user_id, "user.registered", user_id)
        return {"user_id": user_id, "tenant_id": tenant_id}

    def login(self, email: str, password: str) -> dict[str, str]:
        user = self.db.one(
            "SELECT * FROM users WHERE email=? AND active=1", (email.lower().strip(),)
        )
        if not user or not verify_password(password, user["password_hash"]):
            raise AuthError("invalid credentials")
        return self._issue(user["id"], user["tenant_id"])

    def _issue(self, user_id: str, tenant_id: str) -> dict[str, str]:
        scopes = ("chat", "voice", "projects:read")
        access = encode_token(
            new_claims(user_id, tenant_id, scopes, "access", self.settings.access_token_seconds),
            self.settings.jwt_secret,
        )
        refresh_claims = new_claims(
            user_id, tenant_id, scopes, "refresh", self.settings.refresh_token_seconds
        )
        refresh = encode_token(refresh_claims, self.settings.jwt_secret)
        self.db.execute(
            "INSERT INTO refresh_tokens(jti,user_id,token_hash,expires_at) VALUES(?,?,?,?)",
            (refresh_claims.jti, user_id, token_fingerprint(refresh), refresh_claims.expires_at),
        )
        return {"access_token": access, "refresh_token": refresh, "token_type": "bearer"}

    def rotate(self, refresh_token: str) -> dict[str, str]:
        claims = decode_token(refresh_token, self.settings.jwt_secret, "refresh")
        row = self.db.one("SELECT * FROM refresh_tokens WHERE jti=?", (claims.jti,))
        now = int(time.time())
        if not row or row["used_at"] or row["revoked_at"] or row["expires_at"] <= now:
            raise AuthError("refresh token replayed, revoked, or expired")
        if not __import__("hmac").compare_digest(
            row["token_hash"], token_fingerprint(refresh_token)
        ):
            raise AuthError("invalid refresh token")
        self.db.execute("UPDATE refresh_tokens SET used_at=? WHERE jti=?", (now, claims.jti))
        return self._issue(claims.subject, claims.tenant_id)
